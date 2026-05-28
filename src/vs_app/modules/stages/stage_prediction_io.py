from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vs_app.modules.stages.stage_ground_truth import normalize_ticket_key


def read_ticket_ids(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Ticket input not found: {path}")
    return [
        normalize_ticket_key(line.split("#", 1)[0])
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if normalize_ticket_key(line.split("#", 1)[0])
    ]


def load_value_stream_predictions(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Value Stream prediction input not found: {path}")
    if path.suffix.lower() == ".jsonl":
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return {"tickets": {ticket_id_from_record(row): row for row in rows if ticket_id_from_record(row)}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return normalize_vs_prediction_payload(payload)


def normalize_vs_prediction_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return {"tickets": {ticket_id_from_record(row): row for row in payload if ticket_id_from_record(row)}}
    if isinstance(payload, dict) and isinstance(payload.get("tickets"), dict):
        return {
            **payload,
            "tickets": {
                normalize_ticket_key(ticket_id): row
                for ticket_id, row in (payload.get("tickets") or {}).items()
                if normalize_ticket_key(ticket_id)
            },
        }
    if isinstance(payload, dict) and isinstance(payload.get("tickets"), list):
        return {
            **payload,
            "tickets": {
                ticket_id_from_record(row): row
                for row in payload.get("tickets") or []
                if ticket_id_from_record(row)
            },
        }
    if isinstance(payload, dict) and ticket_id_from_record(payload):
        return {"tickets": {ticket_id_from_record(payload): payload}}
    return {"tickets": {}}


def value_stream_prediction_record(vs_predictions: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    return dict((vs_predictions.get("tickets") or {}).get(normalize_ticket_key(ticket_id)) or {})


def predicted_value_streams_for_ticket(
    *,
    vs_predictions: dict[str, Any],
    ticket_id: str,
) -> list[dict[str, Any]]:
    record = value_stream_prediction_record(vs_predictions, ticket_id)
    values = (
        record.get("predicted_value_streams")
        or record.get("selected_value_streams")
        or record.get("value_streams")
        or record.get("predictions")
        or []
    )
    return [
        normalized_value_stream(row)
        for row in ensure_list(values)
        if normalized_value_stream(row).get("name")
    ]


def context_from_prediction_record(ticket_id: str, record: dict[str, Any]) -> dict[str, Any]:
    idea_card = record.get("idea_card") if isinstance(record.get("idea_card"), dict) else {}
    ticket_context = (
        record.get("ticket_context")
        if isinstance(record.get("ticket_context"), dict)
        else {}
    )
    return {
        "ticket_id": ticket_id,
        "summary": clean_text(
            record.get("summary")
            or record.get("idmt_summary")
            or idea_card.get("summary")
            or ticket_context.get("summary")
        ),
        "description": clean_text(
            record.get("description")
            or record.get("idmt_description")
            or idea_card.get("description")
            or ticket_context.get("description")
        ),
        "idea_card_text": clean_text(
            record.get("idea_card_text")
            or record.get("ticket_text")
            or record.get("text")
            or record.get("extracted_text")
            or record.get("attachment_text")
            or idea_card.get("idea_card_text")
            or idea_card.get("text")
            or idea_card.get("extracted_text")
            or idea_card.get("attachment_text")
            or ticket_context.get("idea_card_text")
            or ticket_context.get("extracted_text")
            or ticket_context.get("attachment_text")
        ),
        "attachment_text": clean_text(
            record.get("attachment_text")
            or idea_card.get("attachment_text")
            or ticket_context.get("attachment_text")
        ),
        "extracted_text": clean_text(
            record.get("extracted_text")
            or idea_card.get("extracted_text")
            or ticket_context.get("extracted_text")
        ),
        "generated_summary": clean_text(
            record.get("generated_summary")
            or record.get("llm_summary")
            or record.get("summary_generated")
            or record.get("consolidated_summary")
            or idea_card.get("generated_summary")
            or ticket_context.get("generated_summary")
        ),
    }


def normalized_value_stream(row: Any) -> dict[str, Any]:
    if isinstance(row, str):
        return {"name": clean_text(row), "id": "", "confidence": 0.0, "reason": ""}
    if not isinstance(row, dict):
        return {"name": "", "id": "", "confidence": 0.0, "reason": ""}
    return {
        "name": clean_text(
            row.get("name")
            or row.get("value_stream_name")
            or row.get("entity_name")
            or row.get("vs_name")
            or row.get("business_value_stream")
            or row.get("canonical")
            or row.get("selected_value_stream")
        ),
        "id": clean_text(
            row.get("id")
            or row.get("value_stream_id")
            or row.get("entity_id")
            or row.get("vs_id")
        ),
        "confidence": clamp_float(row.get("confidence") or row.get("score")),
        "reason": clean_text(row.get("reason") or row.get("rationale")),
    }


def ticket_id_from_record(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return normalize_ticket_key(
        record.get("ticket_id")
        or record.get("idmt_key")
        or record.get("ticket_key")
        or record.get("key")
    )


def ensure_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "content" in value:
            return " ".join(coerce_text(item) for item in value.get("content") or [])
        for key in ("text", "value", "name"):
            if value.get(key):
                return str(value.get(key))
        return " ".join(coerce_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(coerce_text(item) for item in value)
    return str(value)


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


__all__ = [
    "clamp_float",
    "clean_text",
    "coerce_text",
    "context_from_prediction_record",
    "ensure_list",
    "load_value_stream_predictions",
    "normalize_vs_prediction_payload",
    "normalized_value_stream",
    "predicted_value_streams_for_ticket",
    "read_ticket_ids",
    "ticket_id_from_record",
    "value_stream_prediction_record",
]
