from __future__ import annotations

from vs_app.modules.value_streams.canonical import (
    canonicalize_foundational_mentions,
    normalize_vs_name,
)


def annotate_foundational_signals(
    candidates: list[dict],
    *,
    foundational_value_streams_canonical: list[str] | None = None,
    foundational_value_stream_entity_ids: list[str] | None = None,
    foundational_value_streams_raw: list[str] | None = None,
) -> list[dict]:
    """Annotate candidates that match explicit trusted anchor metadata.

    Priority:
    1. Match by entity_id when provided.
    2. Match by canonical value-stream name.
    3. Canonicalize explicitly provided raw anchor mentions.

    This function does not scan the current idea-card text and does not
    auto-select. It only adds prompt/debug metadata.
    """
    ids = {
        str(value or "").strip().lower()
        for value in (foundational_value_stream_entity_ids or [])
        if str(value or "").strip()
    }
    canonical_names = {
        normalize_vs_name(value)
        for value in (foundational_value_streams_canonical or [])
        if str(value or "").strip()
    }

    raw_to_canonical: dict[str, tuple[str, str]] = {}

    for value in foundational_value_streams_canonical or []:
        norm = normalize_vs_name(value)
        if norm:
            raw_to_canonical[norm] = (str(value).strip(), "canonical")

    if not canonical_names and foundational_value_streams_raw:
        for item in canonicalize_foundational_mentions(foundational_value_streams_raw):
            norm = normalize_vs_name(item.canonical_name)
            canonical_names.add(norm)
            raw_to_canonical[norm] = (item.raw, item.match_type)

    annotated: list[dict] = []
    for row in candidates:
        out = dict(row)
        entity_id = str(row.get("entity_id") or "").strip().lower()
        name = str(row.get("entity_name") or "").strip()
        name_norm = normalize_vs_name(name)

        if entity_id and entity_id in ids:
            out["foundational_signal"] = True
            out["foundational_match_text"] = name
            out["foundational_match_type"] = "entity_id"
        elif name_norm in canonical_names:
            match_text, match_type = raw_to_canonical.get(name_norm, (name, "canonical"))
            out["foundational_signal"] = True
            out["foundational_match_text"] = match_text
            out["foundational_match_type"] = match_type
        else:
            out.setdefault("foundational_signal", False)

        annotated.append(out)

    return annotated


def foundational_signal_names(
    *,
    foundational_value_streams_canonical: list[str] | None = None,
    foundational_value_streams_raw: list[str] | None = None,
) -> list[str]:
    if foundational_value_streams_canonical:
        return list(dict.fromkeys(foundational_value_streams_canonical))

    if not foundational_value_streams_raw:
        return []

    return list(
        dict.fromkeys(
            item.canonical_name
            for item in canonicalize_foundational_mentions(foundational_value_streams_raw)
        )
    )


def foundational_signal_source(
    *,
    foundational_value_streams_canonical: list[str] | None = None,
    foundational_value_stream_entity_ids: list[str] | None = None,
    foundational_value_streams_raw: list[str] | None = None,
    foundational_signals: list[str] | None = None,
) -> str:
    if foundational_value_streams_canonical or foundational_value_stream_entity_ids:
        return "explicit_request_metadata"
    if foundational_value_streams_raw and foundational_signals:
        return "explicit_request_metadata"
    if foundational_value_streams_raw and not foundational_signals:
        return "explicit_request_metadata_empty"
    return "none"
