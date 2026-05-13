from __future__ import annotations

import re
from typing import Any

from vs_app.modules.value_streams.canonical import (
    canonicalize_value_stream_name,
    normalize_vs_name,
)

_PAREN_AT_END_RE = re.compile(r"\(([^()]+)\)\s*$")
_BRACKETED_CODE_RE = re.compile(r"\s*\[[^\]]+\]\s*")


def clean_title_part(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return text.strip(" -\t\r\n")


def extract_parenthetical_title(title: str) -> str | None:
    match = _PAREN_AT_END_RE.search(clean_title_part(title))
    if not match:
        return None
    value = clean_title_part(match.group(1))
    return value or None


def strip_bracketed_codes(title: str) -> str:
    return clean_title_part(_BRACKETED_CODE_RE.sub(" ", clean_title_part(title)))


def resolve_theme_title_prefix(
    source_ticket_title: str | None,
    ticket_id: str | None,
    *,
    theme_title_prefix: str | None = None,
    theme_title_prefix_source: str | None = None,
) -> tuple[str, str]:
    explicit_prefix = clean_title_part(theme_title_prefix)
    if explicit_prefix:
        return explicit_prefix, clean_title_part(theme_title_prefix_source) or "condensed_theme_title_prefix"

    source_title = clean_title_part(source_ticket_title)

    parenthetical = extract_parenthetical_title(source_title)
    if parenthetical:
        return parenthetical, "parenthetical_product"

    cleaned_source = strip_bracketed_codes(source_title)
    if cleaned_source:
        return cleaned_source, "clean_source_ticket_title"

    ticket = clean_title_part(ticket_id)
    if ticket:
        return ticket, "ticket_id_fallback"
    return "", "unavailable"


def build_value_stream_theme_title(
    theme_prefix: str,
    value_stream_name: str,
    *,
    max_length: int = 255,
) -> str:
    prefix = clean_title_part(theme_prefix)
    stream_name = _canonical_value_stream_name(value_stream_name)
    if not stream_name:
        return prefix[:max(0, max_length)]
    if not prefix:
        return stream_name[:max(0, max_length)]

    suffix = f" - {stream_name}"
    full_title = f"{prefix}{suffix}"
    if len(full_title) <= max_length:
        return full_title

    prefix_budget = max_length - len(suffix)
    if prefix_budget <= 0:
        return suffix[-max_length:]
    if prefix_budget <= 3:
        prefix_part = prefix[:prefix_budget]
    else:
        prefix_part = prefix[: prefix_budget - 3].rstrip() + "..."
    return f"{prefix_part}{suffix}"


def build_theme_identity_key(
    source_ticket_id: str,
    *,
    value_stream_entity_id: str | None = None,
    value_stream_name: str | None = None,
) -> str:
    source_id = str(source_ticket_id or "").strip().upper()
    entity_id = str(value_stream_entity_id or "").strip()
    if entity_id:
        return f"{source_id}::vs_id::{entity_id.lower()}"

    normalized_name = normalize_vs_name(value_stream_name or "")
    return f"{source_id}::vs_name::{normalized_name}"


def build_theme_payload(
    source_ticket_id: str,
    source_ticket_title: str,
    selected_value_stream: dict[str, Any],
    *,
    theme_title_prefix: str | None = None,
    theme_title_prefix_source: str | None = None,
) -> dict[str, Any]:
    raw_name = str(selected_value_stream.get("entity_name") or "").strip()
    value_stream_name = _canonical_value_stream_name(raw_name)
    entity_id = str(selected_value_stream.get("entity_id") or "").strip() or None
    theme_prefix, title_source = resolve_theme_title_prefix(
        source_ticket_title,
        source_ticket_id,
        theme_title_prefix=theme_title_prefix,
        theme_title_prefix_source=theme_title_prefix_source,
    )

    return {
        "identity_key": build_theme_identity_key(
            source_ticket_id,
            value_stream_entity_id=entity_id,
            value_stream_name=value_stream_name,
        ),
        "source_ticket_id": str(source_ticket_id or "").strip(),
        "source_ticket_title": clean_title_part(source_ticket_title),
        "theme_prefix": theme_prefix,
        "value_stream_entity_id": entity_id,
        "value_stream_name": value_stream_name,
        "theme_title": build_value_stream_theme_title(theme_prefix, value_stream_name),
        "confidence": selected_value_stream.get("confidence"),
        "title_source": title_source,
        "selection_source": selected_value_stream.get("selection_source"),
    }


def build_theme_payloads(
    source_ticket_id: str,
    source_ticket_title: str,
    selected_value_streams: list[dict[str, Any]],
    *,
    theme_title_prefix: str | None = None,
    theme_title_prefix_source: str | None = None,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for selected in selected_value_streams or []:
        payload = build_theme_payload(
            source_ticket_id,
            source_ticket_title,
            selected,
            theme_title_prefix=theme_title_prefix,
            theme_title_prefix_source=theme_title_prefix_source,
        )
        if not payload["value_stream_name"]:
            continue
        identity_key = str(payload["identity_key"])
        if identity_key in seen:
            continue
        seen.add(identity_key)
        payloads.append(payload)
    return payloads


def build_stage_child_issue_title(
    theme_prefix: str,
    value_stream_name: str,
    stage_name: str,
    *,
    max_length: int = 255,
) -> str:
    prefix = clean_title_part(theme_prefix)
    stream_name = _canonical_value_stream_name(value_stream_name)
    stage = clean_title_part(stage_name)
    if not stage:
        return build_value_stream_theme_title(prefix, stream_name, max_length=max_length)
    if not prefix and not stream_name:
        return stage[:max(0, max_length)]

    suffix = f" - {stage}"
    head = f"{prefix} : {stream_name}" if prefix and stream_name else prefix or stream_name
    full_title = f"{head}{suffix}"
    if len(full_title) <= max_length:
        return full_title

    head_budget = max_length - len(suffix)
    if head_budget <= 0:
        return suffix[-max_length:]
    if head_budget <= 3:
        head_part = head[:head_budget]
    else:
        head_part = head[: head_budget - 3].rstrip() + "..."
    return f"{head_part}{suffix}"


def enrich_stage_predictions_with_titles(
    *,
    stage_predictions: list[dict],
    theme_payloads: list[dict],
) -> list[dict]:
    themes_by_id = {
        str(row.get("value_stream_entity_id") or "").strip().lower(): row
        for row in theme_payloads or []
        if str(row.get("value_stream_entity_id") or "").strip()
    }
    themes_by_name = {
        normalize_vs_name(str(row.get("value_stream_name") or "")): row
        for row in theme_payloads or []
        if normalize_vs_name(str(row.get("value_stream_name") or ""))
    }

    out: list[dict] = []
    for prediction in stage_predictions or []:
        row = dict(prediction)
        value_stream_id = str(row.get("value_stream_id") or row.get("entity_id") or "").strip().lower()
        value_stream_name = str(row.get("value_stream_name") or row.get("entity_name") or "").strip()
        theme = themes_by_id.get(value_stream_id) or themes_by_name.get(normalize_vs_name(value_stream_name))
        selected_stages: list[dict] = []
        for stage in row.get("selected_stages") or []:
            stage_row = dict(stage)
            if theme:
                theme_prefix = str(theme.get("theme_prefix") or "").strip()
                theme_vs_name = str(theme.get("value_stream_name") or value_stream_name).strip()
                stage_row["theme_prefix"] = theme_prefix
                stage_row["value_stream_name"] = theme_vs_name
                stage_row["stage_child_issue_title"] = build_stage_child_issue_title(
                    theme_prefix,
                    theme_vs_name,
                    str(stage_row.get("stage_name") or ""),
                )
            selected_stages.append(stage_row)
        row["selected_stages"] = selected_stages
        out.append(row)
    return out


def _canonical_value_stream_name(value: str) -> str:
    raw = clean_title_part(value)
    return canonicalize_value_stream_name(raw) or raw
