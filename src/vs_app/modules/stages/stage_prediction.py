from __future__ import annotations

import inspect
import json
from typing import Any

from vs_app.modules.prompts.loader import (
    build_value_stage_prediction_prompt,
    build_value_stage_prediction_system_prompt,
    safe_json_extract,
)
from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage
from vs_app.modules.stages.stage_catalog import (
    get_allowed_stages,
    get_value_stream_catalog_entry,
)


async def predict_stages_for_predicted_value_streams(
    *,
    llm: Any,
    ticket_context: dict[str, Any],
    predicted_value_streams: list[dict[str, Any]],
    stage_catalog: dict[str, Any],
) -> dict[str, Any]:
    ticket_id = clean_text(ticket_context.get("ticket_id"))
    warnings: list[str] = []
    value_stream_predictions: list[dict[str, Any]] = []
    predictions_by_value_stream: dict[str, list[str]] = {}

    for predicted_value_stream in predicted_value_streams or []:
        task = build_stage_prediction_task(
            ticket_context=ticket_context,
            predicted_value_stream=predicted_value_stream,
            stage_catalog=stage_catalog,
        )
        value_stream_name = task["value_stream_name"]
        allowed_stage_defs = list(task.get("allowed_stage_defs") or [])
        allowed_stage_names = list(task.get("allowed_stages") or [])
        row_warnings = list(task.get("warnings") or [])

        if not allowed_stage_names:
            row_warnings.append("no stage catalog entry for predicted value stream")
            value_stream_predictions.append(empty_value_stream_prediction(task, row_warnings))
            predictions_by_value_stream[value_stream_name] = []
            continue

        try:
            parsed, raw_response = await call_stage_prediction_llm(llm=llm, task=task)
        except Exception as exc:
            row_warnings.append(f"stage prediction llm failed: {type(exc).__name__}: {exc}")
            prediction_row = empty_value_stream_prediction(task, row_warnings)
            prediction_row["raw_response"] = ""
            value_stream_predictions.append(prediction_row)
            predictions_by_value_stream[value_stream_name] = []
            continue

        normalized = normalize_stage_prediction(parsed)
        selected, canonical_warnings = canonicalize_predicted_stage_rows(
            rows=normalized["predicted_stages"],
            allowed_stage_defs=allowed_stage_defs or allowed_stage_names,
            value_stream_name=value_stream_name,
        )
        row_warnings.extend(normalized["warnings"])
        row_warnings.extend(canonical_warnings)
        selected = dedupe_stage_rows(selected)

        predictions_by_value_stream[value_stream_name] = [
            row["canonical_stage"]
            for row in selected
            if row.get("canonical_stage")
        ]
        value_stream_predictions.append(
            {
                "value_stream_name": value_stream_name,
                "value_stream_id": task.get("value_stream_id") or "",
                "selected_stages": selected,
                "rejected_stages": normalized["rejected_stages"],
                "allowed_stages": allowed_stage_names,
                "raw_response": raw_response,
                "warnings": row_warnings,
            }
        )

    return {
        "ticket_id": ticket_id,
        "predictions_by_value_stream": predictions_by_value_stream,
        "value_stream_predictions": value_stream_predictions,
        "warnings": warnings,
    }


def build_stage_prediction_task(
    *,
    ticket_context: dict[str, Any],
    predicted_value_stream: dict[str, Any],
    stage_catalog: dict[str, Any],
) -> dict[str, Any]:
    value_stream_name = clean_text(
        predicted_value_stream.get("name")
        or predicted_value_stream.get("value_stream_name")
        or predicted_value_stream.get("business_value_stream")
        or predicted_value_stream.get("canonical")
        or predicted_value_stream.get("selected_value_stream")
    )
    entry = get_value_stream_catalog_entry(value_stream_name, stage_catalog) or {}
    allowed_stage_defs = list(entry.get("stages") or [])
    allowed_stage_names = get_allowed_stages(value_stream_name, stage_catalog)
    approved_stages_json = allowed_stage_payload(allowed_stage_defs or allowed_stage_names)
    value_stream_description = clean_text(
        entry.get("description") or entry.get("value_stream_description")
    )
    system_prompt = build_value_stage_prediction_system_prompt()
    user_prompt = build_value_stage_prediction_prompt(
        ticket_id=clean_text(ticket_context.get("ticket_id")),
        summary=clean_text(ticket_context.get("summary")),
        description=clean_text(ticket_context.get("description")),
        idea_card_text=clean_text(ticket_context.get("idea_card_text")),
        value_stream_name=value_stream_name,
        value_stream_id=clean_text(
            predicted_value_stream.get("id")
            or predicted_value_stream.get("value_stream_id")
            or entry.get("value_stream_id")
        ),
        value_stream_confidence=str(predicted_value_stream.get("confidence") or ""),
        value_stream_reason=clean_text(
            predicted_value_stream.get("reason")
            or predicted_value_stream.get("rationale")
        ),
        value_stream_description=value_stream_description,
        approved_stages_json=approved_stages_json,
    )

    return {
        "ticket_id": clean_text(ticket_context.get("ticket_id")),
        "summary": clean_text(ticket_context.get("summary")),
        "description": clean_text(ticket_context.get("description")),
        "idea_card_text": clean_text(ticket_context.get("idea_card_text")),
        "value_stream_name": value_stream_name,
        "value_stream_id": clean_text(
            predicted_value_stream.get("id")
            or predicted_value_stream.get("value_stream_id")
            or entry.get("value_stream_id")
        ),
        "value_stream_description": value_stream_description,
        "allowed_stage_defs": allowed_stage_defs,
        "allowed_stages": allowed_stage_names,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "warnings": [],
    }


def allowed_stage_payload(stage_defs: list[Any]) -> str:
    rows = [allowed_stage_row(stage) for stage in stage_defs or []]
    return json.dumps(rows, indent=2, ensure_ascii=False)


async def call_stage_prediction_llm(
    *,
    llm: Any,
    task: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if llm is None:
        raise TypeError("llm is required for stage prediction")

    messages = list(task.get("messages") or [])
    prompt = str(task.get("user_prompt") or "")
    system_prompt = str(task.get("system_prompt") or "")

    if hasattr(llm, "invoke"):
        response = llm.invoke(messages)
    elif hasattr(llm, "generate"):
        response = call_generate(llm, prompt, system_prompt=system_prompt)
    elif callable(llm):
        response = llm(messages)
    else:
        raise TypeError("llm must provide invoke(), generate(), or be callable")

    if inspect.isawaitable(response):
        response = await response
    content = getattr(response, "content", response)
    if isinstance(content, dict):
        return content, json.dumps(content, ensure_ascii=False)
    raw_response = str(content or "")
    return parse_stage_prediction_response(raw_response), raw_response


def parse_stage_prediction_response(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if isinstance(response, str):
        return safe_json_extract(response)
    content = getattr(response, "content", response)
    if isinstance(content, dict):
        return content
    return safe_json_extract(str(content or ""))


def normalize_stage_prediction(payload: dict[str, Any]) -> dict[str, Any]:
    predicted_rows = normalize_predicted_stage_rows(
        payload.get("predicted_stages")
        or payload.get("selected_stages")
        or payload.get("picks")
        or []
    )
    rejected_rows = normalize_rejected_stage_rows(payload.get("rejected_stages") or [])
    warnings = [
        clean_text(warning)
        for warning in list(payload.get("warnings") or [])
        if clean_text(warning)
    ]
    return {
        "predicted_stages": predicted_rows,
        "rejected_stages": rejected_rows,
        "warnings": warnings,
    }


def canonicalize_predicted_stage_rows(
    *,
    rows: list[dict[str, Any]],
    allowed_stage_defs: list[Any],
    value_stream_name: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    selected: list[dict[str, Any]] = []
    warnings: list[str] = []

    for row in rows:
        raw_stage = clean_text(row.get("stage_name") or row.get("stage"))
        if not raw_stage:
            continue
        result = canonicalize_stage(
            raw_stage,
            allowed_stage_defs,
            value_stream_name=value_stream_name,
        )
        canonical = clean_text(result.get("canonical"))
        if not canonical:
            warnings.append(f"dropped non-approved stage prediction: {raw_stage}")
            continue
        selected.append(
            {
                "stage_name": raw_stage,
                "confidence": clamp_float(row.get("confidence")),
                "support": normalize_support(row.get("support")),
                "reason": clean_text(row.get("reason")),
                "evidence_summary": clean_text(row.get("evidence_summary")),
                "canonical_stage": canonical,
                "canonical_match_method": clean_text(result.get("match_method")),
                "canonical_confidence": result.get("confidence", 0.0),
            }
        )

    return selected, warnings


def dedupe_stage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_stage: dict[str, dict[str, Any]] = {}
    for row in rows:
        canonical = clean_text(row.get("canonical_stage"))
        if not canonical:
            continue
        existing = by_stage.get(canonical)
        if existing is None:
            by_stage[canonical] = row
            out.append(row)
            continue
        if clamp_float(row.get("confidence")) > clamp_float(existing.get("confidence")):
            existing.update(row)
    return out


def empty_value_stream_prediction(task: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    return {
        "value_stream_name": task.get("value_stream_name") or "",
        "value_stream_id": task.get("value_stream_id") or "",
        "selected_stages": [],
        "rejected_stages": [],
        "allowed_stages": list(task.get("allowed_stages") or []),
        "raw_response": "",
        "warnings": warnings,
    }


def allowed_stage_row(stage: Any) -> dict[str, Any]:
    if not isinstance(stage, dict):
        return {
            "stage_id": "",
            "stage_sequence": None,
            "stage_name": clean_text(stage),
            "stage_display_name": "",
            "stage_description": "",
            "stage_entrance_criteria": "",
            "stage_exit_criteria": "",
            "stage_value_items": "",
            "stage_stakeholders": "",
        }

    return {
        "stage_id": clean_text(stage.get("id") or stage.get("stage_id")),
        "stage_sequence": stage.get("sequence") or stage.get("stage_sequence"),
        "stage_name": clean_text(stage.get("name") or stage.get("stage_name")),
        "stage_display_name": clean_text(
            stage.get("display_name") or stage.get("stage_display_name")
        ),
        "stage_description": clean_text(
            stage.get("description") or stage.get("stage_description")
        ),
        "stage_entrance_criteria": clean_text(
            stage.get("entrance_criteria") or stage.get("stage_entrance_criteria")
        ),
        "stage_exit_criteria": clean_text(
            stage.get("exit_criteria") or stage.get("stage_exit_criteria")
        ),
        "stage_value_items": clean_text(
            stage.get("value_items") or stage.get("stage_value_items")
        ),
        "stage_stakeholders": clean_text(
            stage.get("stakeholders") or stage.get("stage_stakeholders")
        ),
    }


def normalize_predicted_stage_rows(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else [value]
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, str):
            out.append({"stage_name": clean_text(row), "confidence": 0.0})
        elif isinstance(row, dict):
            out.append(
                {
                    "stage_name": clean_text(
                        row.get("stage_name")
                        or row.get("stage")
                        or row.get("name")
                    ),
                    "confidence": clamp_float(row.get("confidence")),
                    "support": clean_text(row.get("support")),
                    "reason": clean_text(row.get("reason")),
                    "evidence_summary": clean_text(row.get("evidence_summary")),
                }
            )
    return [row for row in out if row.get("stage_name")]


def normalize_rejected_stage_rows(value: Any) -> list[dict[str, str]]:
    rows = value if isinstance(value, list) else [value]
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            stage_name = clean_text(row.get("stage_name") or row.get("stage") or row.get("name"))
            reason = clean_text(row.get("reason"))
            if stage_name or reason:
                out.append({"stage_name": stage_name, "reason": reason})
    return out


def call_generate(llm: Any, prompt: str, *, system_prompt: str) -> Any:
    try:
        return llm.generate(prompt, system_prompt=system_prompt)
    except TypeError:
        return llm.generate(prompt)


def normalize_support(value: Any) -> str:
    support = clean_text(value).lower()
    return support if support in {"direct", "implied"} else ""


def clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "allowed_stage_payload",
    "build_stage_prediction_task",
    "call_stage_prediction_llm",
    "canonicalize_predicted_stage_rows",
    "dedupe_stage_rows",
    "normalize_stage_prediction",
    "parse_stage_prediction_response",
    "predict_stages_for_predicted_value_streams",
]
