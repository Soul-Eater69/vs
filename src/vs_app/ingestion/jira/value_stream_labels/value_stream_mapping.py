"""Value stream mapping and canonicalization."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from vs_app.modules.prompts.loader import (
    build_jira_value_stream_verifier_prompt,
    build_jira_value_stream_verifier_system_prompt,
    safe_json_extract,
)
from vs_app.modules.prompts.schemas import VsVerifierResult
from .approved_registry import (
    APPROVED_VALUE_STREAM_SET,
    approved_value_stream_id,
    approved_value_streams_text,
    canonicalize_approved_value_stream,
)
from .helpers import clean_value_stream_name

logger = logging.getLogger(__name__)

_SUMMARY_SEPARATOR_RE = re.compile(r"\s[-\u2013\u2014]\s+|:\s+|-\s+")


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
    return _norm_vs(value) or _norm_vs(clean_value_stream_name(value) or value)



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

    parsed = safe_json_extract(raw)
    try:
        output = VsVerifierResult.model_validate(parsed)
    except ValidationError as exc:
        logger.warning("Jira value-stream verifier output failed schema validation: %s", exc)
        return results

    for mapping in output.mappings:
        key = _mapping_cache_key(mapping.raw_name)
        if not key:
            continue
        candidate = (mapping.approved_value_stream or "").strip()
        resolved = canonicalize_approved_value_stream(candidate) if candidate else None
        if resolved and resolved in APPROVED_VALUE_STREAM_SET:
            results[key] = resolved

    return results


def _candidate_name_variants(raw_name: str, cleaned_name: str | None = None) -> list[str]:
    variants: list[str] = []
    for value in (raw_name, cleaned_name):
        text = str(value or "").strip()
        if not text:
            continue

        variants.append(text)
        for part in _SUMMARY_SEPARATOR_RE.split(text):
            part = part.strip(" -:")
            if part:
                variants.append(part)

        cleaned = clean_value_stream_name(text)
        if cleaned:
            variants.append(cleaned)

    return _dedupe_names(variants)


def _resolve_approved_name(raw_name: str, cleaned_name: str | None = None) -> str | None:
    for candidate in _candidate_name_variants(raw_name, cleaned_name):
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
        resolved = _resolve_approved_name(name, cleaned)

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
        if resolved:
            canonical.append(resolved)
        else:
            logger.warning(
                "Dropped unresolved Jira value-stream name '%s' during canonicalization",
                entry["raw_name"] or entry["cleaned_name"],
            )

    return _dedupe_names(canonical)


def _normalize_theme_summary(summary: str) -> str:
    text = clean_value_stream_name((summary or "").strip())
    return re.sub(r"\s{2,}", " ", text).strip(" :-")


def resolve_value_stream_mapping(
    ticket_data: dict,
    classified_links: dict,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Resolve value stream names/IDs from Jira value-stream issue links.

    Tickets without value-stream issue links are intentionally left unlabeled.
    The ingestion workflow is expected to ingest/evaluate only tickets that have
    verified value-stream links.
    """
    vs_links = list((classified_links or {}).get("vs") or [])
    label_source = "jira_issuelinks"

    vs_links = _dedupe_rows(vs_links)
    if not vs_links:
        return {
            "vs_links": [],
            "vs_ids": [],
            "vs_names": [],
            "jira_group_ids": [],
            "vs_statuses": [],
            "linked_value_streams": [],
            "label_source": label_source,
        }

    verified_links: list[dict] = []
    per_link_names: list[str] = []
    unresolved_entries: list[dict[str, Any]] = []
    for link in vs_links:
        summary = str(link.get("summary") or "")
        raw_summary = str(link.get("summary_raw") or summary)
        cleaned = clean_value_stream_name(summary) or clean_value_stream_name(raw_summary) or summary or raw_summary
        resolved = _resolve_approved_name(raw_summary, cleaned)

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

    linked_value_streams = _dedupe_linked_value_streams(
        [
            {
                "id": approved_value_stream_id(per_link_names[idx]),
                "jira_group_id": str(link.get("key") or ""),
                "name": per_link_names[idx],
                "status": str(link.get("status") or ""),
                "summary_raw": str(link.get("summary_raw") or link.get("summary") or ""),
                "source": label_source,
            }
            for idx, link in enumerate(verified_links)
        ]
    )
    vs_names = [row["name"] for row in linked_value_streams]
    vs_ids = [row["id"] for row in linked_value_streams]
    jira_group_ids = [row["jira_group_id"] for row in linked_value_streams]
    vs_statuses = [row["status"] for row in linked_value_streams]

    return {
        "vs_links": verified_links,
        "vs_ids": vs_ids,
        "vs_names": vs_names,
        "jira_group_ids": jira_group_ids,
        "vs_statuses": vs_statuses,
        "linked_value_streams": linked_value_streams,
        "label_source": label_source,
    }


def resolve_theme_value_stream_mapping(
    themes: list[dict],
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Resolve value-stream labels from implemented-by GROUP/THEME Jira links."""
    label_source = "jira_implemented_by_group_links"
    rows: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    for theme in _dedupe_rows(themes or []):
        summary = str(theme.get("summary") or "")
        raw_summary = str(theme.get("summary_raw") or summary)
        cleaned = clean_value_stream_name(summary) or clean_value_stream_name(raw_summary) or summary or raw_summary
        resolved = _resolve_approved_name(raw_summary, cleaned)
        if resolved:
            rows.append(
                {
                    "id": str(theme.get("key") or ""),
                    "jira_group_id": str(theme.get("key") or ""),
                    "vs_id": approved_value_stream_id(resolved),
                    "name": resolved,
                    "status": str(theme.get("status") or ""),
                    "summary_raw": raw_summary,
                    "source": label_source,
                }
            )
            continue

        unresolved.append(
            {
                "key": str(theme.get("key") or ""),
                "raw_name": raw_summary,
                "cleaned_name": cleaned,
                "status": str(theme.get("status") or ""),
            }
        )

    llm_results = _verify_names_with_llm(unresolved, llm_client=llm_client)
    for entry in unresolved:
        resolved = llm_results.get(_mapping_cache_key(entry["raw_name"]))
        if resolved:
            rows.append(
                {
                    "id": entry["key"],
                    "jira_group_id": entry["key"],
                    "vs_id": approved_value_stream_id(resolved),
                    "name": resolved,
                    "status": entry["status"],
                    "summary_raw": entry["raw_name"],
                    "source": label_source,
                }
            )
        else:
            logger.warning(
                "Dropped unresolved GROUP/THEME value-stream name '%s' (source=%s)",
                entry.get("raw_name") or entry.get("cleaned_name") or "",
                label_source,
            )

    linked_value_streams = _dedupe_linked_value_streams(rows)
    value_stream_names = [row["name"] for row in linked_value_streams]
    value_stream_ids = [row["id"] for row in linked_value_streams]
    jira_group_ids = [row["jira_group_id"] for row in linked_value_streams]
    value_stream_statuses = [row["status"] for row in linked_value_streams]

    return {
        "value_stream_ids": value_stream_ids,
        "value_stream_names": value_stream_names,
        "jira_group_ids": jira_group_ids,
        "value_stream_statuses": value_stream_statuses,
        "linked_value_streams": linked_value_streams,
        "value_stream_label_source": label_source,
        "vs_ids": value_stream_ids,
        "vs_names": value_stream_names,
        "jira_group_ids": jira_group_ids,
        "vs_statuses": value_stream_statuses,
        "label_source": label_source,
    }


def _dedupe_linked_value_streams(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        key = _norm_vs(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "id": str(row.get("vs_id") or row.get("id") or ""),
                "jira_group_id": str(row.get("jira_group_id") or row.get("id") or ""),
                "name": name,
                "status": str(row.get("status") or ""),
                "summary_raw": str(row.get("summary_raw") or ""),
                "source": str(row.get("source") or ""),
            }
        )
    return out
