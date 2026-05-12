from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedThemeValueStream:
    child_title: str
    raw_value_stream_suffix: str
    canonical_name: str
    entity_id: str | None
    match_type: str


def parse_value_stream_from_theme_title(
    *,
    source_title: str,
    child_theme_title: str,
) -> ParsedThemeValueStream | None:
    source = " ".join((source_title or "").split())
    child = " ".join((child_theme_title or "").split())

    if not source or not child:
        return None

    prefix = f"{source} - "
    if not child.lower().startswith(prefix.lower()):
        return None

    suffix = child[len(prefix) :].strip()
    if not suffix:
        return None

    from vs_app.ingestion.jira.value_stream_labels.approved_registry import (
        approved_value_stream_id,
    )
    from vs_app.modules.value_streams.canonical import canonicalize_value_stream_name

    canonical = canonicalize_value_stream_name(suffix)
    if not canonical:
        return None

    match_type = "exact" if suffix.lower() == canonical.lower() else "alias"
    return ParsedThemeValueStream(
        child_title=child,
        raw_value_stream_suffix=suffix,
        canonical_name=canonical,
        entity_id=approved_value_stream_id(canonical) or None,
        match_type=match_type,
    )
