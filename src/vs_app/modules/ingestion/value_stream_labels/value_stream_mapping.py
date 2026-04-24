"""Value stream mapping and canonicalization."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from vs_app.modules.prompts.loader import (
    build_jira_value_stream_verifier_prompt,
    build_jira_value_stream_verifier_system_prompt,
)
from .approved_registry import (
    APPROVED_VALUE_STREAM_SET,
    approved_value_streams_text,
    canonicalize_approved_value_stream,
)
from .helpers import clean_value_stream_name

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dedup / normalization helpers
# ---------------------------------------------------------------------------

def _norm_vs(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (str(row.get("key") or ""), str(row.get("summary") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in names:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = _norm_vs(clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _mapping_cache_key(value: str) -> str:
    return _norm_vs(clean_value_stream_name(value) or value)


def _parse_verifier_json(raw: str) -> dict[str, Any]:
    if not raw:
        return {}

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass

    logger.warning("Failed to parse Jira value-stream verifier JSON: %.200s", raw)
    return {}


def _verify_names_with_llm(
    entries: list[dict[str, str]],
    llm_client: Any | None = None,
) -> dict[str, str]:
    if not entries or llm_client is None:
        return {}

    results: dict[str, str] = {}
    try:
        from vs_app.integrations.llm.client import complete_text
    except Exception as exc:
        logger.info("Jira value-stream verifier text helper unavailable: %s", exc)
        return results

    unresolved_block = []
    for idx, entry in enumerate(entries, start=1):
        unresolved_block.append(
            "\n".join(
                [
                    f"{idx}. raw_name: {entry.get('raw_name', '')}",
                    f"   cleaned_name: {entry.get('cleaned_name', '')}",
                ]
            )
        )

    prompt = build_jira_value_stream_verifier_prompt(
        approved_value_streams=approved_value_streams_text(),
        unresolved_block="\n".join(unresolved_block),
    )

    try:
        raw = complete_text(
            prompt,
            llm_client,
            model="gpt-5-mini-idp",
            max_output_tokens=1200,
            temperature=0.0,
            system_prompt=build_jira_value_stream_verifier_system_prompt(),
        )
    except Exception as exc:
        logger.warning("Jira value-stream verifier LLM call failed: %s", exc)
        return results

    parsed = _parse_verifier_json(raw)
    for item in parsed.get("mappings") or []:
        if not isinstance(item, dict):
            continue

        raw_name = str(item.get("raw_name") or "").strip()
        key = _mapping_cache_key(raw_name)
        if not key:
            continue

        approved_name = item.get("approved_value_stream")
        candidate = str(approved_name or "").strip() if approved_name is not None else ""
        resolved = canonicalize_approved_value_stream(candidate) if candidate else None
        if resolved and resolved in APPROVED_VALUE_STREAM_SET:
            results[key] = resolved

    return results


def _resolve_approved_name(raw_name: str) -> str | None:
    cleaned = clean_value_stream_name(raw_name) or raw_name

    for candidate in (cleaned, raw_name):
        resolved = canonicalize_approved_value_stream(candidate)
        if resolved:
            return resolved

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonicalize_value_stream_names(
    vs_names: list[str],
    llm_client: Any | None = None,
) -> list[str]:
    """Dedupe and normalize names, preferring the approved Jira registry."""
    deduped = _dedupe_names(vs_names)
    if not deduped:
        return []

    canonical: list[str] = []
    unresolved: list[dict[str, str]] = []
    for name in deduped:
        cleaned = clean_value_stream_name(name) or name
        resolved = _resolve_approved_name(name)

        if resolved:
            canonical.append(resolved)
            continue

        unresolved.append(
            {
                "raw_name": name,
                "cleaned_name": cleaned,
            }
        )

    llm_results = _verify_names_with_llm(unresolved, llm_client=llm_client)
    for entry in unresolved:
        resolved = llm_results.get(_mapping_cache_key(entry["raw_name"]))
        canonical.append(resolved or entry["cleaned_name"])

    return _dedupe_names(canonical)


def is_valid_vs_name(name: str) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    if re.fullmatch(r"[A-Z]{1,5}", cleaned):
        return False
    if re.match(r"^(CP|IVL)\s*\d", cleaned):
        return False
    if re.search(r"\b(20|21)\d{2}\b", cleaned):
        return False
    return (" " in cleaned) or len(cleaned) >= 4


def _normalize_theme_summary(summary: str) -> str:
    text = clean_value_stream_name((summary or "").strip())
    return re.sub(r"\s{2,}", " ", text).strip(" :-")


def resolve_value_stream_mapping(
    ticket_data: dict,
    classified_links: dict,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Resolve value stream names/IDs from classified links or theme fallback."""
    vs_links = list((classified_links or {}).get("vs") or [])
    label_source = "jira_issuelinks"

    if not vs_links:
        themes = list((ticket_data or {}).get("themes") or [])
        if themes:
            normalized: list[dict] = []
            for theme in themes:
                issue_type = str(theme.get("issue_type") or "").strip().lower()
                if issue_type and "theme" not in issue_type:
                    continue

                summary_raw = str(theme.get("summary_raw") or theme.get("summary") or "")
                summary = _normalize_theme_summary(summary_raw)
                if not is_valid_vs_name(summary):
                    continue
                normalized.append({
                    "key": str(theme.get("key") or ""),
                    "summary": summary,
                    "summary_raw": summary_raw,
                    "status": str(theme.get("status") or ""),
                    "issue_type": str(theme.get("issue_type") or ""),
                })
            vs_links = normalized
            label_source = "jira_themes_fallback"

    vs_links = _dedupe_rows(vs_links)

    verified_links: list[dict] = []
    per_link_names: list[str] = []
    unresolved_entries: list[dict[str, Any]] = []
    for link in vs_links:
        raw_summary = str(link.get("summary") or "")
        cleaned = clean_value_stream_name(raw_summary) or raw_summary
        resolved = _resolve_approved_name(raw_summary)

        if resolved:
            verified_links.append(link)
            per_link_names.append(resolved)
            continue

        unresolved_entries.append(
            {
                "link": link,
                "raw_name": raw_summary,
                "cleaned_name": cleaned,
            }
        )

    llm_results = _verify_names_with_llm(
        [
            {
                "raw_name": str(entry.get("raw_name") or ""),
                "cleaned_name": str(entry.get("cleaned_name") or ""),
            }
            for entry in unresolved_entries
        ],
        llm_client=llm_client,
    )
    for entry in unresolved_entries:
        resolved = llm_results.get(_mapping_cache_key(str(entry.get("raw_name") or "")))
        if not resolved:
            logger.warning(
                "Dropped unresolved Jira value-stream name '%s' (source=%s)",
                entry.get("raw_name") or entry.get("cleaned_name") or "",
                label_source,
            )
            continue

        verified_links.append(entry["link"])
        per_link_names.append(resolved)

    vs_links = verified_links
    vs_names = _dedupe_names(per_link_names)
    vs_ids = [str(link.get("key") or "") for link in vs_links if str(link.get("key") or "")]
    vs_statuses = [str(link.get("status") or "") for link in vs_links]

    linked_value_streams = [
        {
            "id": str(link.get("key") or ""),
            "name": per_link_names[idx],
            "status": str(link.get("status") or ""),
            "summary_raw": str(link.get("summary_raw") or link.get("summary") or ""),
        }
        for idx, link in enumerate(vs_links)
    ]

    return {
        "vs_links": vs_links,
        "vs_ids": vs_ids,
        "vs_names": vs_names,
        "vs_statuses": vs_statuses,
        "linked_value_streams": linked_value_streams,
        "label_source": label_source,
    }


def resolve_value_stream_epics_mapping(
    ticket_data: dict,
    classified_links: dict,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Extract value streams and their associated epics from ticket data."""
    vs_mapping = resolve_value_stream_mapping(ticket_data, classified_links, llm_client=llm_client)
    linked_value_streams = vs_mapping.get("linked_value_streams", [])

    epics_raw = list((ticket_data or {}).get("epics") or [])

    epics_normalized: list[dict] = []
    seen_epic_keys: set[str] = set()

    for epic in epics_raw:
        key = str(epic.get("key") or "").strip()
        if not key or key in seen_epic_keys:
            continue

        summary_raw = str(epic.get("summary_raw") or epic.get("summary") or "").strip()
        summary = _normalize_theme_summary(summary_raw) if summary_raw else ""

        epics_normalized.append({
            "id": key,
            "key": key,
            "name": summary,
            "summary": summary,
            "summary_raw": summary_raw,
            "status": str(epic.get("status") or "").strip(),
            "type": str(epic.get("type") or "epic").strip(),
        })
        seen_epic_keys.add(key)

    epics_deduped = _dedupe_rows([
        {
            "key": e.get("key"),
            "name": e.get("name"),
            "summary_raw": e.get("summary_raw"),
            "status": e.get("status"),
        }
        for e in epics_normalized
    ])

    vs_with_epics: list[dict] = []
    for vs in linked_value_streams:
        vs_with_epics.append({
            "value_stream": vs,
            "epics": epics_normalized,
            "epic_ids": [e["id"] for e in epics_normalized],
            "epic_count": len(epics_normalized),
        })

    return {
        "value_streams": linked_value_streams,
        "epics_normalized": epics_normalized,
        "epics_deduped": epics_deduped,
        "vs_with_epics": vs_with_epics,
        "all_epic_ids": [e.get("id") for e in epics_normalized],
        "all_epic_names": [e.get("name") for e in epics_normalized if e.get("name")],
        "summary": {
            "num_value_streams": len(linked_value_streams),
            "num_unique_epics": len({e.get("id") for e in epics_normalized}),
            "num_vs_with_epics": len([vs for vs in vs_with_epics if vs.get("epic_ids")]),
        },
    }
