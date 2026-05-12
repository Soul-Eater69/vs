from __future__ import annotations

from typing import Any

from vs_app.modules.rag.query.views import clean_title_part
from vs_app.modules.value_streams.canonical import (
    canonicalize_value_stream_name,
    normalize_vs_name,
)


def build_value_stream_theme_title(
    source_ticket_title: str,
    value_stream_name: str,
    *,
    max_length: int = 255,
) -> str:
    source_title = clean_title_part(source_ticket_title)
    stream_name = _canonical_value_stream_name(value_stream_name)
    if not stream_name:
        return source_title[:max(0, max_length)]
    if not source_title:
        return stream_name[:max(0, max_length)]

    suffix = f" - {stream_name}"
    full_title = f"{source_title}{suffix}"
    if len(full_title) <= max_length:
        return full_title

    source_budget = max_length - len(suffix)
    if source_budget <= 0:
        return suffix[-max_length:]
    if source_budget <= 3:
        source_part = source_title[:source_budget]
    else:
        source_part = source_title[: source_budget - 3].rstrip() + "..."
    return f"{source_part}{suffix}"


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
) -> dict[str, Any]:
    raw_name = str(selected_value_stream.get("entity_name") or "").strip()
    value_stream_name = _canonical_value_stream_name(raw_name)
    entity_id = str(selected_value_stream.get("entity_id") or "").strip() or None

    return {
        "identity_key": build_theme_identity_key(
            source_ticket_id,
            value_stream_entity_id=entity_id,
            value_stream_name=value_stream_name,
        ),
        "source_ticket_id": str(source_ticket_id or "").strip(),
        "source_ticket_title": clean_title_part(source_ticket_title),
        "value_stream_entity_id": entity_id,
        "value_stream_name": value_stream_name,
        "theme_title": build_value_stream_theme_title(source_ticket_title, value_stream_name),
        "confidence": selected_value_stream.get("confidence"),
        "selection_source": selected_value_stream.get("selection_source"),
    }


def build_theme_payloads(
    source_ticket_id: str,
    source_ticket_title: str,
    selected_value_streams: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for selected in selected_value_streams or []:
        payload = build_theme_payload(source_ticket_id, source_ticket_title, selected)
        if not payload["value_stream_name"]:
            continue
        identity_key = str(payload["identity_key"])
        if identity_key in seen:
            continue
        seen.add(identity_key)
        payloads.append(payload)
    return payloads


def _canonical_value_stream_name(value: str) -> str:
    raw = clean_title_part(value)
    return canonicalize_value_stream_name(raw) or raw
