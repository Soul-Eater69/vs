"""Run stage prediction using GT value streams from stage_prediction_dataset.json and evaluate against GT stages.

The evaluator is intentionally stage-only in dataset mode:
- context comes from the dataset row's idea_card fields
- value streams come from ground_truth.gt_by_value_stream keys
- predictions are flushed to disk after every ticket by default
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.build_stage_ground_truth import JiraApiClient
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import normalize_ticket_key
from vs_app.modules.stages.stage_prediction_io import (
    clean_text,
    coerce_text,
    context_from_prediction_record,
    load_value_stream_predictions,
    predicted_value_streams_for_ticket,
    read_ticket_ids,
    value_stream_prediction_record,
)
from vs_app.modules.stages.stage_prediction import predict_stages_for_predicted_value_streams


DEFAULT_TICKETS_INPUT = Path("output/theme_duplicate_scan/clean_ticket_ids.txt")
DEFAULT_GT_INPUT = Path("output/stage_eval/stage_ground_truth.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_OUTPUT_DIR = Path("output/stage_prediction_eval")
DEFAULT_DATASET_INPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
VALID_VS_MODES = {"pipeline", "oracle"}
VALID_CONTEXT_MODES = {"raw", "full", "summary"}

# Legacy fallback for running without stage_prediction_dataset.json.
LEGACY_DEFAULT_VS_INPUT = Path("output/value_stream_predictions.json")

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
    if dataset_payload is not None:
        config["vs_mode"] = "oracle"
    ticket_ids = ticket_ids_for_run(config, dataset_payload)
    if dataset_payload is not None:
        gt_payload = gt_payload_from_dataset(dataset_payload)
        legacy_vs_predictions = {}
    else:
        print(
            "Stage prediction dataset not found; using legacy separate-file VS prediction fallback.",
            flush=True,
        )
        gt_payload = load_json(config["gt_input"])
        legacy_vs_predictions = load_value_stream_predictions(config["legacy_vs_input"])
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")
    llm = make_generation_service()

    print_run_header(
        config=config,
        dataset_payload=dataset_payload,
        ticket_ids=ticket_ids,
        gt_payload=gt_payload,
    )

    jira_client = make_jira_client_if_configured()
    if jira_client is not None:
        async with jira_client:
            result = await run_batch_prediction_eval(
                ticket_ids=ticket_ids,
                gt_payload=gt_payload,
                legacy_vs_predictions=legacy_vs_predictions,
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
            legacy_vs_predictions=legacy_vs_predictions,
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
    vs_mode = clean_text(os.getenv("STAGE_PREDICT_VS_MODE", "oracle")).lower()
    if vs_mode not in VALID_VS_MODES:
        raise SystemExit(
            f"Unsupported STAGE_PREDICT_VS_MODE={vs_mode!r}; expected pipeline or oracle."
        )
    context_mode = clean_text(os.getenv("STAGE_PREDICT_CONTEXT_MODE", "raw")).lower()
    if context_mode not in VALID_CONTEXT_MODES:
        raise SystemExit(
            f"Unsupported STAGE_PREDICT_CONTEXT_MODE={context_mode!r}; expected raw, full, or summary."
        )
    return {
        "tickets_input": Path(os.getenv("STAGE_PREDICT_TICKETS_INPUT", str(DEFAULT_TICKETS_INPUT))),
        "tickets_input_explicit": bool(os.getenv("STAGE_PREDICT_TICKETS_INPUT")),
        "gt_input": Path(os.getenv("STAGE_PREDICT_GT_INPUT", str(DEFAULT_GT_INPUT))),
        # Legacy fallback only. Dataset mode never reads this file.
        "legacy_vs_input": Path(os.getenv("STAGE_PREDICT_VS_INPUT", str(LEGACY_DEFAULT_VS_INPUT))),
        "stage_catalog": Path(os.getenv("STAGE_PREDICT_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
        "output_dir": Path(os.getenv("STAGE_PREDICT_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
        "dataset_input": Path(dataset_env) if dataset_env else DEFAULT_DATASET_INPUT,
        "dataset_input_explicit": bool(dataset_env),
        "vs_mode": vs_mode,
        "context_mode": context_mode,
        "progress": env_flag("STAGE_PREDICT_PROGRESS", default=True),
        "flush_outputs": env_flag("STAGE_PREDICT_FLUSH_OUTPUTS", default=True),
    }


async def run_batch_prediction_eval(
    *,
    ticket_ids: list[str],
    gt_payload: dict[str, Any],
    legacy_vs_predictions: dict[str, Any],
    dataset_payload: dict[str, Any] | None,
    stage_catalog: dict[str, Any],
    llm: Any,
    jira_client: JiraApiClient | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    stage_predictions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    processed_ticket_ids: list[str] = []
    total = len(ticket_ids)

    if config.get("flush_outputs"):
        write_incremental_outputs(
            config=config,
            processed_ticket_ids=processed_ticket_ids,
            all_ticket_ids=ticket_ids,
            gt_payload=gt_payload,
            stage_predictions=stage_predictions,
            errors=errors,
            dataset_payload=dataset_payload,
        )

    for index, ticket_id in enumerate(ticket_ids, start=1):
        ticket_key = normalize_ticket_key(ticket_id)
        start_time = time.perf_counter()
        print_ticket_start(index, total, ticket_key, gt_payload, enabled=bool(config.get("progress")))
        try:
            prediction = await predict_for_ticket(
                ticket_id=ticket_key,
                legacy_vs_predictions=legacy_vs_predictions,
                dataset_payload=dataset_payload,
                vs_mode=str(config["vs_mode"]),
                context_mode=str(config["context_mode"]),
                stage_catalog=stage_catalog,
                llm=llm,
                jira_client=jira_client,
            )
            stage_predictions.append(prediction)
            processed_ticket_ids.append(ticket_key)
            print_ticket_done(
                index,
                total,
                ticket_key,
                prediction,
                elapsed_seconds=time.perf_counter() - start_time,
                enabled=bool(config.get("progress")),
            )
        except Exception as exc:
            error = {"ticket_id": ticket_key, "error": compact_error(exc)}
            errors.append(error)
            processed_ticket_ids.append(ticket_key)
            print_ticket_error(
                index,
                total,
                ticket_key,
                error,
                elapsed_seconds=time.perf_counter() - start_time,
                enabled=bool(config.get("progress")),
            )

        if config.get("flush_outputs"):
            write_incremental_outputs(
                config=config,
                processed_ticket_ids=processed_ticket_ids,
                all_ticket_ids=ticket_ids,
                gt_payload=gt_payload,
                stage_predictions=stage_predictions,
                errors=errors,
                dataset_payload=dataset_payload,
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

    return build_result_payload(
        config=config,
        dataset_payload=dataset_payload,
        stage_predictions=stage_predictions,
        eval_rows=eval_rows,
        errors=errors,
        summary=summary,
    )


async def predict_for_ticket(
    *,
    ticket_id: str,
    legacy_vs_predictions: dict[str, Any],
    dataset_payload: dict[str, Any] | None = None,
    vs_mode: str = "oracle",
    context_mode: str = "raw",
    stage_catalog: dict[str, Any],
    llm: Any,
    jira_client: JiraApiClient | None,
) -> dict[str, Any]:
    ticket_key = normalize_ticket_key(ticket_id)
    if dataset_payload is not None:
        dataset_record = dataset_ticket_record(dataset_payload, ticket_key)
        oracle_value_stream_targets = oracle_value_streams_from_ground_truth(
            dataset_record.get("ground_truth") or {}
        )
        ticket_context = await ticket_context_from_dataset_ticket(
            ticket_id=ticket_key,
            dataset_record=dataset_record,
            jira_client=jira_client,
            context_mode=context_mode,
        )
        value_stream_targets = oracle_value_stream_targets
    else:
        # Legacy fallback: use a separate value-stream prediction file when the dataset is absent.
        prediction_record = value_stream_prediction_record(legacy_vs_predictions, ticket_key)
        legacy_pipeline_value_streams = predicted_value_streams_for_ticket(
            vs_predictions=legacy_vs_predictions,
            ticket_id=ticket_key,
        )
        ticket_context = await ticket_context_for_prediction(
            ticket_id=ticket_key,
            prediction_record=prediction_record,
            jira_client=jira_client,
        )
        value_stream_targets = legacy_pipeline_value_streams
    return await predict_stages_for_predicted_value_streams(
        llm=llm,
        ticket_context=ticket_context,
        predicted_value_streams=value_stream_targets,
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


def build_result_payload(
    *,
    config: dict[str, Any],
    dataset_payload: dict[str, Any] | None,
    stage_predictions: list[dict[str, Any]],
    eval_rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "predictions_payload": {
            "source": "stage_prediction",
            "generated_at": utc_now(),
            "tickets_input": str(config["tickets_input"]),
            "legacy_vs_input": "" if dataset_payload is not None else str(config["legacy_vs_input"]),
            "dataset_input": str(config["dataset_input"]) if dataset_payload is not None else "",
            "vs_mode": str(config["vs_mode"]),
            "context_mode": str(config["context_mode"]),
            "stage_catalog": str(config["stage_catalog"]),
            "tickets": stage_predictions,
            "errors": errors,
        },
        "eval_payload": {
            "source": "stage_prediction_eval",
            "generated_at": utc_now(),
            "tickets_input": str(config["tickets_input"]),
            "gt_input": str(config["gt_input"]),
            "legacy_vs_input": "" if dataset_payload is not None else str(config["legacy_vs_input"]),
            "dataset_input": str(config["dataset_input"]) if dataset_payload is not None else "",
            "vs_mode": str(config["vs_mode"]),
            "context_mode": str(config["context_mode"]),
            "stage_catalog": str(config["stage_catalog"]),
            "summary": summary,
            "rows": eval_rows,
            "errors": errors,
        },
    }


def write_incremental_outputs(
    *,
    config: dict[str, Any],
    processed_ticket_ids: list[str],
    all_ticket_ids: list[str],
    gt_payload: dict[str, Any],
    stage_predictions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    dataset_payload: dict[str, Any] | None,
) -> None:
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_rows = evaluate_stage_predictions(
        ticket_ids=processed_ticket_ids,
        gt_payload=gt_payload,
        stage_predictions=stage_predictions,
    )
    summary = build_eval_summary(
        ticket_ids=all_ticket_ids,
        stage_predictions=stage_predictions,
        eval_rows=eval_rows,
        errors=errors,
    )
    result = build_result_payload(
        config=config,
        dataset_payload=dataset_payload,
        stage_predictions=stage_predictions,
        eval_rows=eval_rows,
        errors=errors,
        summary=summary,
    )
    write_outputs(result, output_dir)


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


def print_run_header(
    *,
    config: dict[str, Any],
    dataset_payload: dict[str, Any] | None,
    ticket_ids: list[str],
    gt_payload: dict[str, Any],
) -> None:
    if not config.get("progress"):
        return
    gt_ticket_count = sum(
        1
        for row in (gt_payload.get("tickets") or {}).values()
        if (row.get("gt_by_value_stream") or {})
    )
    print("Stage prediction eval starting", flush=True)
    print(f"Tickets selected: {len(ticket_ids)}", flush=True)
    print(f"Tickets with GT in payload: {gt_ticket_count}", flush=True)
    print(
        f"Dataset input: {config['dataset_input'] if dataset_payload is not None else 'not used'}",
        flush=True,
    )
    print(
        f"Tickets input: {config['tickets_input'] if config.get('tickets_input_explicit') else 'dataset_gt_keys'}",
        flush=True,
    )
    print(f"Context mode: {config['context_mode']}", flush=True)
    print(f"Stage catalog: {config['stage_catalog']}", flush=True)
    print(f"Output directory: {config['output_dir']}", flush=True)
    print(f"Flush outputs per ticket: {config.get('flush_outputs')}", flush=True)


def print_ticket_start(
    index: int,
    total: int,
    ticket_id: str,
    gt_payload: dict[str, Any],
    *,
    enabled: bool,
) -> None:
    if not enabled:
        return
    gt_ticket = (gt_payload.get("tickets") or {}).get(ticket_id) or {}
    gt_by_vs = gt_ticket.get("gt_by_value_stream") or {}
    gt_stage_count = sum(len(stages or []) for stages in gt_by_vs.values())
    print(
        f"[{index}/{total}] START {ticket_id} | gt_vs={len(gt_by_vs)} | gt_stages={gt_stage_count}",
        flush=True,
    )


def print_ticket_done(
    index: int,
    total: int,
    ticket_id: str,
    prediction: dict[str, Any],
    *,
    elapsed_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return
    predictions_by_vs = prediction.get("predictions_by_value_stream") or {}
    predicted_stage_count = sum(len(stages or []) for stages in predictions_by_vs.values())
    warnings_count = sum(
        len((row.get("warnings") or []))
        for row in (prediction.get("value_stream_predictions") or [])
        if isinstance(row, dict)
    )
    print(
        f"[{index}/{total}] DONE {ticket_id} | "
        f"pred_vs={len(predictions_by_vs)} | "
        f"pred_stages={predicted_stage_count} | "
        f"warnings={warnings_count} | "
        f"seconds={elapsed_seconds:.1f}",
        flush=True,
    )


def print_ticket_error(
    index: int,
    total: int,
    ticket_id: str,
    error: dict[str, Any],
    *,
    elapsed_seconds: float,
    enabled: bool,
) -> None:
    if not enabled:
        return
    print(
        f"[{index}/{total}] ERROR {ticket_id} | {error.get('error')} | seconds={elapsed_seconds:.1f}",
        flush=True,
    )


def print_summary(result: dict[str, Any], output_dir: Path) -> None:
    summary = result["eval_payload"]["summary"]
    print("Stage prediction eval complete", flush=True)
    print(f"Tickets requested: {summary['tickets_requested']}", flush=True)
    print(f"Tickets evaluated: {summary['tickets_evaluated']}", flush=True)
    print(f"Rows evaluated: {summary['rows_evaluated']}", flush=True)
    print(f"Errors: {summary['errors']}", flush=True)
    print(f"Micro precision: {summary['micro_precision']:.3f}", flush=True)
    print(f"Micro recall: {summary['micro_recall']:.3f}", flush=True)
    print(f"Micro F1: {summary['micro_f1']:.3f}", flush=True)
    print(f"Macro precision: {summary['macro_precision']:.3f}", flush=True)
    print(f"Macro recall: {summary['macro_recall']:.3f}", flush=True)
    print(f"Macro F1: {summary['macro_f1']:.3f}", flush=True)
    print(f"Output directory: {output_dir}", flush=True)


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
    if config.get("tickets_input_explicit"):
        return read_ticket_ids(Path(config["tickets_input"]))
    with_gt = ticket_ids_from_dataset_with_gt(dataset_payload)
    return with_gt or ticket_ids_from_dataset(dataset_payload)


def ticket_ids_from_dataset(dataset_payload: dict[str, Any]) -> list[str]:
    return [
        normalize_ticket_key(ticket_id)
        for ticket_id in (dataset_payload.get("tickets") or {}).keys()
        if normalize_ticket_key(ticket_id)
    ]


def ticket_ids_from_dataset_with_gt(dataset_payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for ticket_id, row in (dataset_payload.get("tickets") or {}).items():
        ticket_key = normalize_ticket_key(ticket_id)
        if not ticket_key or not isinstance(row, dict):
            continue
        gt_by_vs = ((row.get("ground_truth") or {}).get("gt_by_value_stream") or {})
        if gt_by_vs:
            out.append(ticket_key)
    return out


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
    context_mode: str = "raw",
) -> dict[str, Any]:
    idea_card = dataset_record.get("idea_card") or {}
    context = {
        "ticket_id": ticket_id,
        "summary": clean_text(idea_card.get("summary")),
        "description": clean_text(idea_card.get("description")),
        "idea_card_text": dataset_idea_card_text(
            idea_card,
            context_mode=context_mode,
        ),
        "generated_summary": clean_text(idea_card.get("generated_summary")),
    }
    if context.get("summary") or context.get("description") or context.get("idea_card_text"):
        return context
    return await ticket_context_for_prediction(
        ticket_id=ticket_id,
        prediction_record={},
        jira_client=jira_client,
    )


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


def dataset_idea_card_text(
    idea_card: dict[str, Any],
    *,
    context_mode: str = "raw",
) -> str:
    if context_mode == "summary":
        keys = ("generated_summary",)
    elif context_mode == "full":
        keys = ("idea_card_text", "attachment_text", "extracted_text", "generated_summary")
    else:
        keys = ("idea_card_text", "attachment_text", "extracted_text")

    parts: list[str] = []
    seen: set[str] = set()
    for key in keys:
        text = clean_text(idea_card.get(key))
        if text and text not in seen:
            seen.add(text)
            parts.append(text)

    if not parts and context_mode == "raw":
        generated_summary = clean_text(idea_card.get("generated_summary"))
        if generated_summary:
            parts.append(generated_summary)

    return "\n\n".join(parts)


def prediction_debug_for_value_stream(
    prediction: dict[str, Any],
    value_stream_name: str,
) -> dict[str, Any]:
    for row in prediction.get("value_stream_predictions") or []:
        if row.get("value_stream_name") == value_stream_name:
            return row
    return {}


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
        f"Context mode: {eval_payload.get('context_mode', '')}",
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


def load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


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


def env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
