"""Run stage prediction from existing value-stream predictions and evaluate it."""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.build_stage_ground_truth import JiraApiClient
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import normalize_ticket_key
from vs_app.modules.stages.stage_prediction import predict_stages_for_predicted_value_streams


DEFAULT_TICKETS_INPUT = Path("output/theme_duplicate_scan/clean_ticket_ids.txt")
DEFAULT_GT_INPUT = Path("output/stage_eval/stage_ground_truth.json")
DEFAULT_VS_INPUT = Path("output/value_stream_predictions.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_OUTPUT_DIR = Path("output/stage_prediction_eval")
DEFAULT_DATASET_INPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
VALID_VS_MODES = {"pipeline", "oracle"}

PREDICTIONS_JSON = "stage_predictions.json"
PREDICTIONS_JSONL = "stage_predictions.jsonl"
EVAL_JSON = "stage_prediction_eval.json"
EVAL_CSV = "stage_prediction_eval.csv"
SUMMARY_TXT = "summary.txt"
ERRORS_JSON = "errors.json"

CSV_COLUMNS = [
    "idmt_key",
    "value_stream_name",
    "gt_stages",
    "predicted_stages",
    "tp",
    "fp",
    "fn",
    "precision",
    "recall",
    "f1",
    "exact_set_match",
    "prediction_confidences",
    "prediction_support",
    "prediction_reasons",
    "warnings",
]


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    config = load_runtime_config()
    dataset_payload = load_stage_prediction_dataset_if_available(config)
    ticket_ids = ticket_ids_for_run(config, dataset_payload)
    if dataset_payload is not None:
        gt_payload = gt_payload_from_dataset(dataset_payload)
        vs_predictions = {}
    else:
        gt_payload = load_json(config["gt_input"])
        vs_predictions = load_value_stream_predictions(config["vs_input"])
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")
    llm = make_generation_service()

    jira_client = make_jira_client_if_configured()
    if jira_client is not None:
        async with jira_client:
            result = await run_batch_prediction_eval(
                ticket_ids=ticket_ids,
                gt_payload=gt_payload,
                vs_predictions=vs_predictions,
                dataset_payload=dataset_payload,
                stage_catalog=stage_catalog,
                llm=llm,
                jira_client=jira_client,
                config=config,
            )
    else:
        result = await run_batch_prediction_eval(
            ticket_ids=ticket_ids,
            gt_payload=gt_payload,
            vs_predictions=vs_predictions,
            dataset_payload=dataset_payload,
            stage_catalog=stage_catalog,
            llm=llm,
            jira_client=None,
            config=config,
        )

    write_outputs(result, config["output_dir"])
    print_summary(result, config["output_dir"])
    return 0


def load_runtime_config() -> dict[str, Any]:
    dataset_env = os.getenv("STAGE_PREDICT_DATASET_INPUT")
    vs_mode = clean_text(os.getenv("STAGE_PREDICT_VS_MODE", "pipeline")).lower()
    if vs_mode not in VALID_VS_MODES:
        raise SystemExit(
            f"Unsupported STAGE_PREDICT_VS_MODE={vs_mode!r}; expected pipeline or oracle."
        )
    return {
        "tickets_input": Path(os.getenv("STAGE_PREDICT_TICKETS_INPUT", str(DEFAULT_TICKETS_INPUT))),
        "tickets_input_explicit": bool(os.getenv("STAGE_PREDICT_TICKETS_INPUT")),
        "gt_input": Path(os.getenv("STAGE_PREDICT_GT_INPUT", str(DEFAULT_GT_INPUT))),
        "vs_input": Path(os.getenv("STAGE_PREDICT_VS_INPUT", str(DEFAULT_VS_INPUT))),
        "stage_catalog": Path(os.getenv("STAGE_PREDICT_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
        "output_dir": Path(os.getenv("STAGE_PREDICT_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        "dataset_input": Path(dataset_env) if dataset_env else DEFAULT_DATASET_INPUT,
        "dataset_input_explicit": bool(dataset_env),
        "vs_mode": vs_mode,
    }


async def run_batch_prediction_eval(
    *,
    ticket_ids: list[str],
    gt_payload: dict[str, Any],
    vs_predictions: dict[str, Any],
    dataset_payload: dict[str, Any] | None,
    stage_catalog: dict[str, Any],
    llm: Any,
    jira_client: JiraApiClient | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    stage_predictions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for ticket_id in ticket_ids:
        try:
            prediction = await predict_for_ticket(
                ticket_id=ticket_id,
                vs_predictions=vs_predictions,
                dataset_payload=dataset_payload,
                vs_mode=str(config["vs_mode"]),
                stage_catalog=stage_catalog,
                llm=llm,
                jira_client=jira_client,
            )
            stage_predictions.append(prediction)
        except Exception as exc:
            errors.append(
                {
                    "ticket_id": ticket_id,
                    "error": compact_error(exc),
                }
            )

    eval_rows = evaluate_stage_predictions(
        ticket_ids=ticket_ids,
        gt_payload=gt_payload,
        stage_predictions=stage_predictions,
    )
    summary = build_eval_summary(
        ticket_ids=ticket_ids,
        stage_predictions=stage_predictions,
        eval_rows=eval_rows,
        errors=errors,
    )

    return {
        "predictions_payload": {
            "source": "stage_prediction",
            "generated_at": utc_now(),
            "tickets_input": str(config["tickets_input"]),
            "vs_input": str(config["vs_input"]),
            "dataset_input": str(config["dataset_input"]) if dataset_payload is not None else "",
            "vs_mode": str(config["vs_mode"]),
            "stage_catalog": str(config["stage_catalog"]),
            "tickets": stage_predictions,
            "errors": errors,
        },
        "eval_payload": {
            "source": "stage_prediction_eval",
            "generated_at": utc_now(),
            "tickets_input": str(config["tickets_input"]),
            "gt_input": str(config["gt_input"]),
            "vs_input": str(config["vs_input"]),
            "dataset_input": str(config["dataset_input"]) if dataset_payload is not None else "",
            "vs_mode": str(config["vs_mode"]),
            "stage_catalog": str(config["stage_catalog"]),
            "summary": summary,
            "rows": eval_rows,
            "errors": errors,
        },
    }


async def predict_for_ticket(
    *,
    ticket_id: str,
    vs_predictions: dict[str, Any],
    dataset_payload: dict[str, Any] | None = None,
    vs_mode: str = "pipeline",
    stage_catalog: dict[str, Any],
    llm: Any,
    jira_client: JiraApiClient | None,
) -> dict[str, Any]:
    ticket_key = normalize_ticket_key(ticket_id)
    if dataset_payload is not None:
        dataset_record = dataset_ticket_record(dataset_payload, ticket_key)
        predicted_value_streams = predicted_value_streams_from_dataset_ticket(
            dataset_record,
            vs_mode=vs_mode,
        )
        ticket_context = await ticket_context_from_dataset_ticket(
            ticket_id=ticket_key,
            dataset_record=dataset_record,
            jira_client=jira_client,
        )
    else:
        prediction_record = value_stream_prediction_record(vs_predictions, ticket_key)
        predicted_value_streams = predicted_value_streams_for_ticket(
            vs_predictions=vs_predictions,
            ticket_id=ticket_key,
        )
        ticket_context = await ticket_context_for_prediction(
            ticket_id=ticket_key,
            prediction_record=prediction_record,
            jira_client=jira_client,
        )
    return await predict_stages_for_predicted_value_streams(
        llm=llm,
        ticket_context=ticket_context,
        predicted_value_streams=predicted_value_streams,
        stage_catalog=stage_catalog,
    )


async def ticket_context_for_prediction(
    *,
    ticket_id: str,
    prediction_record: dict[str, Any],
    jira_client: JiraApiClient | None,
) -> dict[str, Any]:
    context = context_from_prediction_record(ticket_id, prediction_record)
    if context.get("summary") or context.get("description") or context.get("idea_card_text"):
        return context
    if jira_client is None:
        raise RuntimeError(
            f"ticket context missing for {ticket_id}; provide summary/description in VS input "
            "or set JIRA_BASE_URL and JIRA_TOKEN"
        )
    issue = await jira_client.get_issue(
        ticket_id,
        fields=["summary", "description"],
        expand=False,
    )
    fields = issue.get("fields") or {}
    return {
        "ticket_id": ticket_id,
        "summary": clean_text(fields.get("summary")),
        "description": clean_text(coerce_text(fields.get("description"))),
        "idea_card_text": "",
    }


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
    payload = load_json(path)
    return normalize_vs_prediction_payload(payload)


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


def evaluate_stage_predictions(
    *,
    ticket_ids: list[str],
    gt_payload: dict[str, Any],
    stage_predictions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    predictions_by_ticket = {
        normalize_ticket_key(prediction.get("ticket_id")): prediction
        for prediction in stage_predictions
    }
    rows: list[dict[str, Any]] = []
    for ticket_id in ticket_ids:
        ticket_key = normalize_ticket_key(ticket_id)
        gt_ticket = (gt_payload.get("tickets") or {}).get(ticket_key) or {}
        prediction = predictions_by_ticket.get(ticket_key) or {}
        value_stream_names = sorted(
            set((gt_ticket.get("gt_by_value_stream") or {}).keys())
            | set((prediction.get("predictions_by_value_stream") or {}).keys())
        )
        for value_stream_name in value_stream_names:
            rows.append(
                evaluate_prediction_row(
                    ticket_id=ticket_key,
                    value_stream_name=value_stream_name,
                    gt_ticket=gt_ticket,
                    prediction=prediction,
                )
            )
    return rows


def evaluate_prediction_row(
    *,
    ticket_id: str,
    value_stream_name: str,
    gt_ticket: dict[str, Any],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    gt_stages = clean_list((gt_ticket.get("gt_by_value_stream") or {}).get(value_stream_name) or [])
    predicted_stages = clean_list(
        (prediction.get("predictions_by_value_stream") or {}).get(value_stream_name) or []
    )
    gt_set = set(gt_stages)
    pred_set = set(predicted_stages)
    tp = sorted(gt_set & pred_set)
    fp = sorted(pred_set - gt_set)
    fn = sorted(gt_set - pred_set)
    precision = safe_div(len(tp), len(tp) + len(fp))
    recall = safe_div(len(tp), len(tp) + len(fn))
    f1 = f1_score(precision, recall)
    debug = prediction_debug_for_value_stream(prediction, value_stream_name)

    return {
        "idmt_key": ticket_id,
        "value_stream_name": value_stream_name,
        "gt_stages": sorted(gt_set),
        "predicted_stages": sorted(pred_set),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "exact_set_match": gt_set == pred_set,
        "theme_keys_from_gt": theme_keys_from_gt(gt_ticket, value_stream_name),
        "prediction_debug": debug,
    }


def build_eval_summary(
    *,
    ticket_ids: list[str],
    stage_predictions: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    total_tp = sum(len(row.get("tp") or []) for row in eval_rows)
    total_fp = sum(len(row.get("fp") or []) for row in eval_rows)
    total_fn = sum(len(row.get("fn") or []) for row in eval_rows)
    micro_precision = safe_div(total_tp, total_tp + total_fp)
    micro_recall = safe_div(total_tp, total_tp + total_fn)
    exact_matches = sum(1 for row in eval_rows if row.get("exact_set_match"))

    return {
        "tickets_requested": len(ticket_ids),
        "tickets_evaluated": len(stage_predictions),
        "rows_evaluated": len(eval_rows),
        "evaluated_rows": len(eval_rows),
        "empty_gt_empty_pred_rows": sum(
            1
            for row in eval_rows
            if not row.get("gt_stages") and not row.get("predicted_stages")
        ),
        "errors": len(errors),
        "micro_precision": micro_precision,
        "micro_recall": micro_recall,
        "micro_f1": f1_score(micro_precision, micro_recall),
        "macro_precision": avg(row.get("precision") for row in eval_rows),
        "macro_recall": avg(row.get("recall") for row in eval_rows),
        "macro_f1": avg(row.get("f1") for row in eval_rows),
        "exact_set_match_rate": safe_div(exact_matches, len(eval_rows)),
    }


def write_outputs(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_payload = result["predictions_payload"]
    eval_payload = result["eval_payload"]
    errors = list(eval_payload.get("errors") or [])

    write_json(output_dir / PREDICTIONS_JSON, predictions_payload)
    write_jsonl(output_dir / PREDICTIONS_JSONL, predictions_payload.get("tickets") or [])
    write_json(output_dir / EVAL_JSON, eval_payload)
    write_eval_csv(output_dir / EVAL_CSV, eval_payload.get("rows") or [])
    write_summary_text(output_dir / SUMMARY_TXT, eval_payload, output_dir)
    write_json(output_dir / ERRORS_JSON, {"errors": errors})


def print_summary(result: dict[str, Any], output_dir: Path) -> None:
    summary = result["eval_payload"]["summary"]
    print("Stage prediction eval complete")
    print(f"Tickets requested: {summary['tickets_requested']}")
    print(f"Tickets evaluated: {summary['tickets_evaluated']}")
    print(f"Rows evaluated: {summary['rows_evaluated']}")
    print(f"Errors: {summary['errors']}")
    print(f"Micro precision: {summary['micro_precision']:.3f}")
    print(f"Micro recall: {summary['micro_recall']:.3f}")
    print(f"Micro F1: {summary['micro_f1']:.3f}")
    print(f"Macro precision: {summary['macro_precision']:.3f}")
    print(f"Macro recall: {summary['macro_recall']:.3f}")
    print(f"Macro F1: {summary['macro_f1']:.3f}")
    print(f"Output directory: {output_dir}")


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
            or idea_card.get("idea_card_text")
            or idea_card.get("text")
            or ticket_context.get("idea_card_text")
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


def load_stage_prediction_dataset_if_available(config: dict[str, Any]) -> dict[str, Any] | None:
    path = Path(config["dataset_input"])
    if path.exists():
        return load_json(path)
    if config.get("dataset_input_explicit"):
        raise SystemExit(f"Stage prediction dataset input not found: {path}")
    return None


def ticket_ids_for_run(config: dict[str, Any], dataset_payload: dict[str, Any] | None) -> list[str]:
    if dataset_payload is None:
        return read_ticket_ids(Path(config["tickets_input"]))
    tickets_input = Path(config["tickets_input"])
    if config.get("tickets_input_explicit") or tickets_input.exists():
        return read_ticket_ids(tickets_input)
    return ticket_ids_from_dataset(dataset_payload)


def ticket_ids_from_dataset(dataset_payload: dict[str, Any]) -> list[str]:
    return [
        normalize_ticket_key(ticket_id)
        for ticket_id in (dataset_payload.get("tickets") or {}).keys()
        if normalize_ticket_key(ticket_id)
    ]


def gt_payload_from_dataset(dataset_payload: dict[str, Any]) -> dict[str, Any]:
    tickets: dict[str, Any] = {}
    for ticket_id, row in (dataset_payload.get("tickets") or {}).items():
        ticket_key = normalize_ticket_key(ticket_id)
        if not ticket_key or not isinstance(row, dict):
            continue
        tickets[ticket_key] = dict(row.get("ground_truth") or {})
    return {
        "source": "stage_prediction_dataset",
        "generated_at": dataset_payload.get("generated_at") or "",
        "tickets": tickets,
    }


def dataset_ticket_record(dataset_payload: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    ticket_key = normalize_ticket_key(ticket_id)
    direct = (dataset_payload.get("tickets") or {}).get(ticket_key)
    if isinstance(direct, dict):
        return direct
    for key, row in (dataset_payload.get("tickets") or {}).items():
        if normalize_ticket_key(key) == ticket_key and isinstance(row, dict):
            return row
    return {}


async def ticket_context_from_dataset_ticket(
    *,
    ticket_id: str,
    dataset_record: dict[str, Any],
    jira_client: JiraApiClient | None,
) -> dict[str, Any]:
    idea_card = dataset_record.get("idea_card") or {}
    context = {
        "ticket_id": ticket_id,
        "summary": clean_text(idea_card.get("summary")),
        "description": clean_text(idea_card.get("description")),
        "idea_card_text": clean_text(idea_card.get("idea_card_text")),
        "generated_summary": clean_text(idea_card.get("generated_summary")),
    }
    if context.get("summary") or context.get("description") or context.get("idea_card_text"):
        return context
    return await ticket_context_for_prediction(
        ticket_id=ticket_id,
        prediction_record={},
        jira_client=jira_client,
    )


def predicted_value_streams_from_dataset_ticket(
    dataset_record: dict[str, Any],
    *,
    vs_mode: str,
) -> list[dict[str, Any]]:
    mode = clean_text(vs_mode).lower() or "pipeline"
    if mode == "oracle":
        return oracle_value_streams_from_ground_truth(dataset_record.get("ground_truth") or {})
    return [
        normalized_value_stream(row)
        for row in ensure_list(dataset_record.get("predicted_value_streams") or [])
        if normalized_value_stream(row).get("name")
    ]


def oracle_value_streams_from_ground_truth(ground_truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": clean_text(value_stream_name),
            "id": "",
            "confidence": 1.0,
            "reason": "oracle value stream from stage ground truth",
        }
        for value_stream_name in (ground_truth.get("gt_by_value_stream") or {}).keys()
        if clean_text(value_stream_name)
    ]


def normalized_value_stream(row: Any) -> dict[str, Any]:
    if isinstance(row, str):
        return {"name": clean_text(row), "id": "", "confidence": 0.0, "reason": ""}
    if not isinstance(row, dict):
        return {"name": "", "id": "", "confidence": 0.0, "reason": ""}
    return {
        "name": clean_text(
            row.get("name")
            or row.get("value_stream_name")
            or row.get("business_value_stream")
            or row.get("canonical")
            or row.get("selected_value_stream")
        ),
        "id": clean_text(row.get("id") or row.get("value_stream_id")),
        "confidence": clamp_float(row.get("confidence") or row.get("score")),
        "reason": clean_text(row.get("reason") or row.get("rationale")),
    }


def prediction_debug_for_value_stream(
    prediction: dict[str, Any],
    value_stream_name: str,
) -> dict[str, Any]:
    for row in prediction.get("value_stream_predictions") or []:
        if row.get("value_stream_name") == value_stream_name:
            return row
    return {}


def theme_keys_from_gt(gt_ticket: dict[str, Any], value_stream_name: str) -> list[str]:
    keys: list[str] = []
    for theme in gt_ticket.get("linked_themes") or []:
        if (theme.get("business_value_stream") or {}).get("name") == value_stream_name:
            key = clean_text(theme.get("theme_key"))
            if key:
                keys.append(key)
    return keys


def write_eval_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(eval_csv_row(row))


def eval_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    debug = row.get("prediction_debug") or {}
    selected = list(debug.get("selected_stages") or [])
    return {
        "idmt_key": row.get("idmt_key") or "",
        "value_stream_name": row.get("value_stream_name") or "",
        "gt_stages": join_list(row.get("gt_stages") or []),
        "predicted_stages": join_list(row.get("predicted_stages") or []),
        "tp": join_list(row.get("tp") or []),
        "fp": join_list(row.get("fp") or []),
        "fn": join_list(row.get("fn") or []),
        "precision": row.get("precision"),
        "recall": row.get("recall"),
        "f1": row.get("f1"),
        "exact_set_match": row.get("exact_set_match"),
        "prediction_confidences": join_list([stage.get("confidence") for stage in selected]),
        "prediction_support": join_list([stage.get("support") for stage in selected]),
        "prediction_reasons": join_list([stage.get("reason") for stage in selected]),
        "warnings": join_list(debug.get("warnings") or []),
    }


def write_summary_text(path: Path, eval_payload: dict[str, Any], output_dir: Path) -> None:
    summary = eval_payload.get("summary") or {}
    rows = list(eval_payload.get("rows") or [])
    lines = [
        "Stage Prediction Evaluation Summary",
        f"Tickets requested: {summary.get('tickets_requested', 0)}",
        f"Tickets evaluated: {summary.get('tickets_evaluated', 0)}",
        f"Rows evaluated: {summary.get('rows_evaluated', 0)}",
        f"Errors: {summary.get('errors', 0)}",
        f"Micro precision: {summary.get('micro_precision', 0.0):.3f}",
        f"Micro recall: {summary.get('micro_recall', 0.0):.3f}",
        f"Micro F1: {summary.get('micro_f1', 0.0):.3f}",
        f"Macro precision: {summary.get('macro_precision', 0.0):.3f}",
        f"Macro recall: {summary.get('macro_recall', 0.0):.3f}",
        f"Macro F1: {summary.get('macro_f1', 0.0):.3f}",
        f"Exact set match rate: {summary.get('exact_set_match_rate', 0.0):.3f}",
        "",
        "Worst recall rows:",
        *worst_rows(rows, "recall"),
        "",
        "Worst precision rows:",
        *worst_rows(rows, "precision"),
        "",
        "Output files:",
        str(output_dir / PREDICTIONS_JSON),
        str(output_dir / PREDICTIONS_JSONL),
        str(output_dir / EVAL_JSON),
        str(output_dir / EVAL_CSV),
        str(output_dir / SUMMARY_TXT),
        str(output_dir / ERRORS_JSON),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def worst_rows(rows: list[dict[str, Any]], metric: str, limit: int = 10) -> list[str]:
    candidates = [
        row
        for row in rows
        if row.get("gt_stages") or row.get("predicted_stages")
    ]
    ordered = sorted(candidates, key=lambda row: (float(row.get(metric) or 0.0), row.get("idmt_key") or ""))
    return [
        (
            f"{row.get('idmt_key')} | {row.get('value_stream_name')} | "
            f"{metric}={float(row.get(metric) or 0.0):.3f} | "
            f"gt={join_list(row.get('gt_stages') or [])} | pred={join_list(row.get('predicted_stages') or [])}"
        )
        for row in ordered[:limit]
    ]


def make_generation_service() -> Any:
    if not os.getenv("LLM_BASE_URL") and not os.getenv("OPENAI_API_BASE"):
        raise SystemExit("Stage prediction requires LLM_BASE_URL or OPENAI_API_BASE.")
    try:
        from vs_app.integrations.clients.generation_service import GenerationService
    except Exception as exc:
        raise SystemExit(f"Unable to load GenerationService for stage prediction: {exc}") from exc
    return GenerationService()


def make_jira_client_if_configured() -> JiraApiClient | None:
    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    token = os.getenv("JIRA_TOKEN", "").strip()
    if not base_url or not token:
        return None
    return JiraApiClient(base_url=base_url, token=token, verify_ssl=False)


def read_ticket_ids(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Ticket input not found: {path}")
    return [
        normalize_ticket_key(line.split("#", 1)[0])
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if normalize_ticket_key(line.split("#", 1)[0])
    ]


def load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


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


def clean_list(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def join_list(values: list[Any]) -> str:
    return " | ".join(clean_text(value) for value in values if clean_text(value))


def safe_div(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def f1_score(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0


def avg(values: Any) -> float:
    vals = [float(value or 0.0) for value in values]
    return round(sum(vals) / len(vals), 6) if vals else 0.0


def clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


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


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
