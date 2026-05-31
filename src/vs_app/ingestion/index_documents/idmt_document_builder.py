"""Build the canonical Azure-ready IDMT document.

This is the single source of truth for the shape of an indexed IDMT ticket.
The prediction input is the original IDMT packet (summary, description, idea
card, attachment/extracted text, generated summary). Ground truth comes from
Jira artifacts: Value Streams are Theme/GROUP issues and stages are Epics under
each Theme. Epic titles are answer-key artifacts and are never fed back as
prediction input here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from vs_app.ingestion.index_documents.models import (
    StageSupport,
    TicketContext,
    ValueStreamSupport,
)

DEFAULT_SOURCE_SYSTEM = "jira"
DEFAULT_INDEX_VERSION = "idmt-v2-stage-history"


def build_indexed_idmt_document(
    *,
    ticket_context: TicketContext,
    value_stream_support: list[ValueStreamSupport],
    gt_by_value_stream: dict[str, list[str]],
    stage_support: list[StageSupport] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert an IDMT packet plus Jira GT into one Azure-ready document."""
    ticket_id = normalize_ticket_id(ticket_context.ticket_id)

    summary = clean_label_text(ticket_context.summary)
    generated_summary = clean_label_text(ticket_context.generated_summary)
    description = clean_document_text(ticket_context.description)
    idea_card_text = clean_document_text(ticket_context.idea_card_text)
    attachment_text = clean_document_text(ticket_context.attachment_text)
    extracted_text = clean_document_text(ticket_context.extracted_text)
    retrieval_text = clean_document_text(ticket_context.retrieval_text) or build_retrieval_text(
        summary, generated_summary, description, extracted_text
    )

    gt_map = clean_gt_by_value_stream(gt_by_value_stream)
    vs_rows = clean_value_stream_support(value_stream_support)
    stage_rows = merge_stage_support_with_gt(clean_stage_support(stage_support), gt_map)

    valid_vs_names = build_value_stream_names(vs_rows, gt_map)
    valid_vs_ids = dedupe_text(row["value_stream_id"] for row in vs_rows)
    vs_pairs = build_value_stream_pairs(valid_vs_names, vs_rows)

    stage_names = build_stage_names(gt_map)
    stage_ids = dedupe_text(row["stage_id"] for row in stage_rows)
    stage_pairs = build_stage_pairs(gt_map)

    meta = resolve_metadata(metadata)

    return {
        "id": ticket_id,
        "ticket_id": ticket_id,
        "ticket_key": ticket_id,
        "summary": summary,
        "description": description,
        "idea_card_text": idea_card_text,
        "attachment_text": attachment_text,
        "extracted_text": extracted_text,
        "generated_summary": generated_summary,
        "retrieval_text": retrieval_text,
        "valid_value_stream_names": valid_vs_names,
        "valid_value_stream_ids": valid_vs_ids,
        "value_stream_pairs": vs_pairs,
        "value_stream_support": vs_rows,
        "value_stream_support_text": build_value_stream_support_text(vs_rows),
        "gt_by_value_stream_json": build_gt_by_value_stream_json(gt_map),
        "stage_names": stage_names,
        "stage_ids": stage_ids,
        "stage_pairs": stage_pairs,
        "stage_history_text": build_stage_history_text(gt_map),
        "stage_support": stage_rows,
        "stage_support_text": build_stage_support_text(stage_rows),
        "has_valid_value_streams": bool(valid_vs_names),
        "valid_value_stream_count": len(valid_vs_names),
        "has_stage_gt": bool(stage_names),
        "stage_count": len(stage_names),
        "source_system": meta["source_system"],
        "index_version": meta["index_version"],
        "ingested_at": meta["ingested_at"],
        "warnings": meta["warnings"],
    }


def build_document_from_stage_dataset_row(
    ticket_id: str,
    row: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt an existing stage-prediction dataset row into an indexed document.

    Lets already-generated dataset JSON convert to Azure-ready documents without
    regenerating summaries. Value Stream support starts as ``unknown`` because we
    only know the BA-approved Value Streams from Jira GT, not yet how the original
    context supports them.
    """
    idea_card = row.get("idea_card") or {}
    ground_truth = row.get("ground_truth") or {}
    gt_by_value_stream = ground_truth.get("gt_by_value_stream") or {}
    warnings = row.get("warnings") or []

    ticket_context = TicketContext(
        ticket_id=ticket_id,
        summary=idea_card.get("summary", ""),
        description=idea_card.get("description", ""),
        idea_card_text=idea_card.get("idea_card_text", ""),
        attachment_text=idea_card.get("attachment_text", ""),
        extracted_text=idea_card.get("extracted_text", ""),
        generated_summary=idea_card.get("generated_summary", ""),
    )
    value_stream_support = [
        ValueStreamSupport(
            value_stream_name=value_stream_name,
            support_type="unknown",
            source="jira_gt",
        )
        for value_stream_name in gt_by_value_stream
    ]

    doc_metadata = {"warnings": warnings}
    if metadata:
        doc_metadata.update(metadata)

    return build_indexed_idmt_document(
        ticket_context=ticket_context,
        value_stream_support=value_stream_support,
        gt_by_value_stream=gt_by_value_stream,
        metadata=doc_metadata,
    )


# --- text helpers -----------------------------------------------------------


def clean_label_text(value: Any) -> str:
    """Collapse all whitespace to single spaces. For names, IDs, and support fields."""
    if value is None:
        return ""
    return " ".join(str(value).split())


def clean_document_text(value: Any) -> str:
    """Clean prose while preserving line breaks.

    Used for description / idea card / attachment / extracted text where the
    line structure carries meaning. Collapses intra-line whitespace, trims each
    line, and squeezes blank-line runs down to a single blank line.
    """
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def dedupe_text(values: Iterable[Any]) -> list[str]:
    """Clean each value, drop empties, and dedupe while preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = clean_label_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_ticket_id(ticket_id: Any) -> str:
    return clean_label_text(ticket_id).upper()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


RETRIEVAL_TEXT_MAX_CHARS = 4000
RETRIEVAL_TEXT_MIN_CHARS = 200


def build_retrieval_text(
    summary: str,
    generated_summary: str,
    description: str,
    extracted_text: str,
) -> str:
    """Build compact retrieval text, capped at ``RETRIEVAL_TEXT_MAX_CHARS``.

    Prefers the curated context (summary, generated summary, description) and only
    falls back to raw extracted text when that context is too thin to retrieve on.
    """
    parts = [part for part in (summary, generated_summary, description) if part]
    text = "\n\n".join(parts)
    if len(text) < RETRIEVAL_TEXT_MIN_CHARS and extracted_text:
        text = "\n\n".join([*parts, extracted_text])
    return text[:RETRIEVAL_TEXT_MAX_CHARS].strip()


# --- value stream / stage cleaning ------------------------------------------


def clean_gt_by_value_stream(
    gt_by_value_stream: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Clean Value Stream names and dedupe their stages, dropping empties."""
    cleaned: dict[str, list[str]] = {}
    for raw_name, raw_stages in (gt_by_value_stream or {}).items():
        name = clean_label_text(raw_name)
        if not name:
            continue
        existing = cleaned.setdefault(name, [])
        for stage in dedupe_text(raw_stages or []):
            if stage not in existing:
                existing.append(stage)
    return cleaned


def clean_value_stream_support(rows: list[ValueStreamSupport] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        name = clean_label_text(row.value_stream_name)
        if not name:
            continue
        cleaned.append(
            {
                "value_stream_name": name,
                "value_stream_id": clean_label_text(row.value_stream_id),
                "support_type": clean_label_text(row.support_type) or "unknown",
                "reason": clean_label_text(row.reason),
                "evidence": clean_label_text(row.evidence),
                "source": clean_label_text(row.source),
                "confidence": row.confidence,
            }
        )
    return dedupe_rows(cleaned)


def clean_stage_support(rows: list[StageSupport] | None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows or []:
        value_stream_name = clean_label_text(row.value_stream_name)
        stage_name = clean_label_text(row.stage_name)
        if not value_stream_name or not stage_name:
            continue
        cleaned.append(
            {
                "value_stream_name": value_stream_name,
                "value_stream_id": clean_label_text(row.value_stream_id),
                "stage_name": stage_name,
                "stage_id": clean_label_text(row.stage_id),
                "support_type": clean_label_text(row.support_type) or "unknown",
                "reason": clean_label_text(row.reason),
                "evidence": clean_label_text(row.evidence),
                "source": clean_label_text(row.source),
                "confidence": row.confidence,
            }
        )
    return dedupe_rows(cleaned)


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop identical support rows while preserving order."""
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def merge_stage_support_with_gt(
    stage_rows: list[dict[str, Any]],
    gt_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Keep classified stage support and add unknown fallback rows for GT stages.

    Every BA-created stage from Jira GT must appear. Provided rows are kept as-is;
    any GT (value stream, stage) pair not already covered gets an ``unknown`` /
    ``jira_gt`` row, since we know the stage exists but not yet whether the
    original context directly or implicitly supports it.
    """
    covered = {(row["value_stream_name"], row["stage_name"]) for row in stage_rows}
    merged = list(stage_rows)
    for value_stream_name in sorted(gt_map):
        for stage_name in sorted(gt_map[value_stream_name]):
            if (value_stream_name, stage_name) not in covered:
                merged.append(unknown_stage_row(value_stream_name, stage_name))
    return merged


def unknown_stage_row(value_stream_name: str, stage_name: str) -> dict[str, Any]:
    """A GT stage whose support has not been classified yet."""
    return {
        "value_stream_name": value_stream_name,
        "value_stream_id": "",
        "stage_name": stage_name,
        "stage_id": "",
        "support_type": "unknown",
        "reason": "",
        "evidence": "",
        "source": "jira_gt",
        "confidence": None,
    }


# --- derived list / text fields ---------------------------------------------


def build_value_stream_names(
    vs_rows: list[dict[str, Any]],
    gt_map: dict[str, list[str]],
) -> list[str]:
    names = [row["value_stream_name"] for row in vs_rows]
    names.extend(gt_map.keys())
    return dedupe_text(names)


def build_value_stream_pairs(
    valid_vs_names: list[str],
    vs_rows: list[dict[str, Any]],
) -> list[str]:
    """``value_stream_id::value_stream_name`` per VS, or the name when no ID."""
    id_by_name: dict[str, str] = {}
    for row in vs_rows:
        name, vs_id = row["value_stream_name"], row["value_stream_id"]
        if vs_id and name not in id_by_name:
            id_by_name[name] = vs_id
    pairs = []
    for name in valid_vs_names:
        vs_id = id_by_name.get(name, "")
        pairs.append(f"{vs_id}::{name}" if vs_id else name)
    return pairs


def build_stage_names(gt_map: dict[str, list[str]]) -> list[str]:
    names: list[str] = []
    for value_stream_name in sorted(gt_map):
        for stage_name in sorted(gt_map[value_stream_name]):
            if stage_name not in names:
                names.append(stage_name)
    return names


def build_stage_pairs(gt_map: dict[str, list[str]]) -> list[str]:
    pairs: list[str] = []
    for value_stream_name in sorted(gt_map):
        for stage_name in sorted(gt_map[value_stream_name]):
            pairs.append(f"{value_stream_name}::{stage_name}")
    return dedupe_text(pairs)


def build_gt_by_value_stream_json(gt_map: dict[str, list[str]]) -> str:
    """Deterministic JSON: sorted Value Stream keys and sorted stages."""
    ordered = {name: sorted(gt_map[name]) for name in sorted(gt_map)}
    return json.dumps(ordered, ensure_ascii=False, sort_keys=True)


def build_stage_history_text(gt_map: dict[str, list[str]]) -> str:
    """Compact human-readable history for prompt evidence."""
    sentences: list[str] = []
    for value_stream_name in sorted(gt_map):
        stages = sorted(gt_map[value_stream_name])
        if not stages:
            continue
        if len(stages) == 1:
            sentences.append(
                f"For Value Stream {value_stream_name}, BA-created stage was {stages[0]}."
            )
        else:
            sentences.append(
                f"For Value Stream {value_stream_name}, "
                f"BA-created stages were {join_with_and(stages)}."
            )
    return " ".join(sentences)


def build_value_stream_support_text(vs_rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in vs_rows:
        segment = [f"VS {row['value_stream_name']}", f"support {row['support_type']}"]
        if row["evidence"]:
            segment.append(f"evidence {row['evidence']}")
        if row["reason"]:
            segment.append(f"reason {row['reason']}")
        parts.append(" ".join(segment))
    return " | ".join(parts)


def build_stage_support_text(stage_rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in stage_rows:
        segment = [
            f"VS {row['value_stream_name']}",
            f"stage {row['stage_name']}",
            f"support {row['support_type']}",
            f"source {row['source']}",
        ]
        if row["reason"]:
            segment.append(f"reason {row['reason']}")
        if row["evidence"]:
            segment.append(f"evidence {row['evidence']}")
        parts.append(" ".join(segment))
    return " | ".join(parts)


def join_with_and(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


# --- metadata ----------------------------------------------------------------


def resolve_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "source_system": DEFAULT_SOURCE_SYSTEM,
        "index_version": DEFAULT_INDEX_VERSION,
        "ingested_at": utc_now(),
        "warnings": [],
    }
    if metadata:
        for key in ("source_system", "index_version", "ingested_at"):
            if metadata.get(key):
                resolved[key] = clean_label_text(metadata[key])
        if "warnings" in metadata:
            resolved["warnings"] = dedupe_text(metadata["warnings"] or [])
    return resolved


__all__ = [
    "build_indexed_idmt_document",
    "build_document_from_stage_dataset_row",
    "clean_label_text",
    "clean_document_text",
    "dedupe_text",
    "normalize_ticket_id",
    "utc_now",
    "clean_gt_by_value_stream",
    "merge_stage_support_with_gt",
    "build_stage_pairs",
    "build_stage_history_text",
    "build_gt_by_value_stream_json",
    "build_value_stream_support_text",
    "build_stage_support_text",
]
