"""Pure builder for Theme-generation POC index documents.

Produces, from one ticket's historic data, a single ``idmt`` document plus one
``theme`` document per linked Theme/GROUP. The IDMT document is the vector-search
document; theme documents are readable prompt examples (no ``content_vector``).

Pure and deterministic: no Jira/LLM/Azure calls. Stable IDs:
    idmt::{ticket_id}
    theme::{ticket_id}::{group_id}

This does not modify the canonical IDMT document builder; it is a separate POC
shape (see module ``theme_generation_schema``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_VALID_SUPPORT_TYPES = ("direct", "implied")


@dataclass
class ThemeGenerationValueStream:
    group_id: str = ""
    value_stream_id: str = ""
    value_stream_name: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""


@dataclass
class ThemeGenerationStage:
    epic_id: str = ""
    stage_id: str = ""
    stage_name: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""


@dataclass
class ThemeGenerationThemeInput:
    group_id: str = ""
    theme_description: str = ""
    business_needs: str = ""
    stages: list[ThemeGenerationStage] = field(default_factory=list)


def build_theme_generation_documents(
    *,
    ticket_id: str,
    summary_text: str = "",
    idmt_description: str = "",
    key_terms: list[str] | None = None,
    stakeholders: list[str] | None = None,
    systems_and_products: list[str] | None = None,
    value_streams: list[ThemeGenerationValueStream] | None = None,
    themes: list[ThemeGenerationThemeInput] | None = None,
    idmt_content_vector: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Build one IDMT document plus one theme document per theme/group."""
    ticket = _clean(ticket_id)
    summary_text = _clean(summary_text)
    idmt_description = _clean(idmt_description)
    key_terms = _clean_list(key_terms)
    stakeholders = _clean_list(stakeholders)
    systems_and_products = _clean_list(systems_and_products)
    vs_rows = [_value_stream_row(vs) for vs in (value_streams or [])]

    idmt_properties = {
        "summary_text": summary_text,
        "idmt_description": idmt_description,
        "theme_description": "",
        "business_needs": "",
        "key_terms": key_terms,
        "stakeholders": stakeholders,
        "systems_and_products": systems_and_products,
        "value_streams": vs_rows,
        "stages": [],
    }
    idmt_doc: dict[str, Any] = {
        "id": f"idmt::{ticket}",
        "document_type": "idmt",
        "ticket_id": ticket,
        "group_id": "",
        "content": _idmt_content(idmt_properties),
        "properties": idmt_properties,
    }
    if idmt_content_vector is not None:
        idmt_doc["content_vector"] = [float(value) for value in idmt_content_vector]

    documents: list[dict[str, Any]] = [idmt_doc]

    for theme in themes or []:
        group_id = _clean(theme.group_id)
        matched_vs = _match_value_stream(vs_rows, group_id)
        stage_rows = [_stage_row(stage) for stage in theme.stages]
        theme_properties = {
            # Parent IDMT context, copied for standalone readability.
            "summary_text": summary_text,
            "idmt_description": idmt_description,
            "key_terms": key_terms,
            "stakeholders": stakeholders,
            "systems_and_products": systems_and_products,
            # Theme-specific fields.
            "theme_description": _clean(theme.theme_description),
            "business_needs": _clean(theme.business_needs),
            "value_streams": [matched_vs] if matched_vs else [],
            "stages": stage_rows,
        }
        documents.append(
            {
                "id": f"theme::{ticket}::{group_id}",
                "document_type": "theme",
                "ticket_id": ticket,
                "group_id": group_id,
                "content": _theme_content(theme_properties),
                "properties": theme_properties,
            }
        )

    return documents


def _value_stream_row(vs: ThemeGenerationValueStream) -> dict[str, str]:
    return {
        "group_id": _clean(vs.group_id),
        "value_stream_id": _clean(vs.value_stream_id),
        "value_stream_name": _clean(vs.value_stream_name),
        "support_type": _support_type(vs.support_type),
        "reason": _clean(vs.reason),
        "evidence": _clean(vs.evidence),
    }


def _stage_row(stage: ThemeGenerationStage) -> dict[str, str]:
    return {
        "epic_id": _clean(stage.epic_id),
        "stage_id": _clean(stage.stage_id),
        "stage_name": _clean(stage.stage_name),
        "support_type": _support_type(stage.support_type),
        "reason": _clean(stage.reason),
        "evidence": _clean(stage.evidence),
    }


def _match_value_stream(
    vs_rows: list[dict[str, str]], group_id: str
) -> dict[str, str] | None:
    """Return the single value stream row whose group_id matches this theme."""
    for row in vs_rows:
        if row["group_id"] == group_id:
            return row
    return None


def _idmt_content(properties: dict[str, Any]) -> str:
    parts: list[str] = []
    _append(parts, properties["summary_text"])
    _append(parts, properties["idmt_description"])
    _append(parts, _labelled("Key Terms", properties["key_terms"]))
    _append(parts, _labelled("Stakeholders", properties["stakeholders"]))
    _append(parts, _labelled("Systems & Products", properties["systems_and_products"]))
    vs_names = [row["value_stream_name"] for row in properties["value_streams"] if row["value_stream_name"]]
    _append(parts, _labelled("Value Streams", vs_names))
    return "\n".join(parts).strip()


def _theme_content(properties: dict[str, Any]) -> str:
    parts: list[str] = []
    value_streams = properties["value_streams"]
    if value_streams and value_streams[0]["value_stream_name"]:
        _append(parts, f"Value Stream: {value_streams[0]['value_stream_name']}")
    _append(parts, properties["theme_description"])
    if properties["business_needs"]:
        _append(parts, f"Business Needs: {properties['business_needs']}")
    stage_names = [row["stage_name"] for row in properties["stages"] if row["stage_name"]]
    _append(parts, _labelled("Stages", stage_names))
    # Parent IDMT context for standalone readability.
    if properties["summary_text"]:
        _append(parts, f"IDMT Summary: {properties['summary_text']}")
    if properties["idmt_description"]:
        _append(parts, f"IDMT Description: {properties['idmt_description']}")
    return "\n".join(parts).strip()


def _labelled(label: str, items: list[str]) -> str:
    return f"{label}: {', '.join(items)}" if items else ""


def _append(parts: list[str], text: str) -> None:
    if text:
        parts.append(text)


def _support_type(value: Any) -> str:
    """Normalize support to ``direct`` / ``implied``; anything else becomes ``""``.

    The row is kept (for traceability) but an invalid/missing support type is
    blanked rather than guessed.
    """
    cleaned = _clean(value).lower()
    return cleaned if cleaned in _VALID_SUPPORT_TYPES else ""


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_list(values: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


__all__ = [
    "ThemeGenerationValueStream",
    "ThemeGenerationStage",
    "ThemeGenerationThemeInput",
    "build_theme_generation_documents",
]
