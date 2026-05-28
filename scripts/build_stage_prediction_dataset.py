"""Build a unified dataset for stage prediction and evaluation."""

from __future__ import annotations

import asyncio
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
from scripts.evaluate_stage_prediction_batch import (
    clean_text,
    coerce_text,
    context_from_prediction_record,
    load_value_stream_predictions,
    predicted_value_streams_for_ticket,
    read_ticket_ids,
    value_stream_prediction_record,
)
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    build_ticket_stage_ground_truth,
    normalize_ticket_key,
)


DEFAULT_TICKETS_INPUT = Path("output/theme_duplicate_scan/clean_ticket_ids.txt")
DEFAULT_VS_INPUT = Path("output/value_stream_predictions.json")
DEFAULT_GT_INPUT = Path("output/stage_eval/stage_ground_truth.json")
DEFAULT_OUTPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    config = load_runtime_config()
    ticket_ids = read_ticket_ids(config["tickets_input"])
    vs_predictions = load_value_stream_predictions(config["vs_input"])
    gt_payload = load_gt_payload_if_available(config["gt_input"])
    jira_client = make_jira_client_if_configured()

    if jira_client is not None:
        async with jira_client:
            dataset = await build_stage_prediction_dataset(
                ticket_ids=ticket_ids,
                vs_predictions=vs_predictions,
                gt_payload=gt_payload,
                jira_client=jira_client,
                config=config,
            )
    else:
        dataset = await build_stage_prediction_dataset(
            ticket_ids=ticket_ids,
            vs_predictions=vs_predictions,
            gt_payload=gt_payload,
            jira_client=None,
            config=config,
        )

    write_dataset_outputs(dataset, config["output"])
    print_dataset_summary(dataset, config["output"])
    return 0


def load_runtime_config() -> dict[str, Path]:
    return {
        "tickets_input": Path(os.getenv("STAGE_DATASET_TICKETS_INPUT", str(DEFAULT_TICKETS_INPUT))),
        "vs_input": Path(os.getenv("STAGE_DATASET_VS_INPUT", str(DEFAULT_VS_INPUT))),
        "gt_input": Path(os.getenv("STAGE_DATASET_GT_INPUT", str(DEFAULT_GT_INPUT))),
        "output": Path(os.getenv("STAGE_DATASET_OUTPUT", str(DEFAULT_OUTPUT))),
        "stage_catalog": Path(os.getenv("STAGE_DATASET_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
    }


async def build_stage_prediction_dataset(
    *,
    ticket_ids: list[str],
    vs_predictions: dict[str, Any],
    gt_payload: dict[str, Any],
    jira_client: JiraApiClient | None,
    config: dict[str, Path],
) -> dict[str, Any]:
    tickets: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    stage_catalog: dict[str, Any] | None = None

    for ticket_id in ticket_ids:
        ticket_key = normalize_ticket_key(ticket_id)
        try:
            ticket_row, stage_catalog = await build_dataset_ticket(
                ticket_id=ticket_key,
                vs_predictions=vs_predictions,
                gt_payload=gt_payload,
                jira_client=jira_client,
                stage_catalog=stage_catalog,
                stage_catalog_path=config["stage_catalog"],
            )
            tickets[ticket_key] = ticket_row
        except Exception as exc:
            errors.append(
                {
                    "ticket_id": ticket_key,
                    "error": compact_error(exc),
                }
            )

    return {
        "source": "stage_prediction_dataset",
        "generated_at": utc_now(),
        "tickets_input": str(config["tickets_input"]),
        "vs_input": str(config["vs_input"]),
        "gt_input": str(config["gt_input"]),
        "tickets": tickets,
        "errors": errors,
    }


async def build_dataset_ticket(
    *,
    ticket_id: str,
    vs_predictions: dict[str, Any],
    gt_payload: dict[str, Any],
    jira_client: JiraApiClient | None,
    stage_catalog: dict[str, Any] | None,
    stage_catalog_path: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    warnings: list[str] = []
    prediction_record = value_stream_prediction_record(vs_predictions, ticket_id)
    idea_card = await idea_card_for_ticket(
        ticket_id=ticket_id,
        prediction_record=prediction_record,
        jira_client=jira_client,
        warnings=warnings,
    )
    predicted_value_streams = predicted_value_streams_for_ticket(
        vs_predictions=vs_predictions,
        ticket_id=ticket_id,
    )
    if not predicted_value_streams:
        warnings.append("predicted Value Streams missing")

    ground_truth, gt_found = ground_truth_for_ticket(gt_payload, ticket_id)
    if not gt_found:
        if jira_client is not None:
            if stage_catalog is None:
                stage_catalog = load_stage_catalog(path=stage_catalog_path, source="json")
            ground_truth = normalize_ground_truth_ticket(
                await build_ticket_stage_ground_truth(
                    ticket_key=ticket_id,
                    jira_client=jira_client,
                    catalog=stage_catalog,
                )
            )
        else:
            warnings.append("stage ground truth missing")

    if not has_prediction_context(idea_card):
        warnings.append("IDMT idea-card context missing")
    if not gt_found and not has_ground_truth(ground_truth):
        warnings.append("stage ground truth unavailable")

    return (
        {
            "ticket_id": ticket_id,
            "idea_card": idea_card,
            "predicted_value_streams": predicted_value_streams,
            "ground_truth": ground_truth,
            "warnings": dedupe_text(warnings),
        },
        stage_catalog,
    )


async def idea_card_for_ticket(
    *,
    ticket_id: str,
    prediction_record: dict[str, Any],
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> dict[str, str]:
    context = context_from_prediction_record(ticket_id, prediction_record)
    idea_card = normalize_idea_card(context)
    if has_prediction_context(idea_card):
        return idea_card

    if jira_client is None:
        warnings.append(
            "IDMT context missing from VS prediction record and Jira credentials are unavailable"
        )
        return idea_card

    issue = await jira_client.get_issue(
        ticket_id,
        fields=["summary", "description"],
        expand=False,
    )
    fields = issue.get("fields") or {}
    return {
        "summary": clean_text(fields.get("summary")),
        "description": clean_text(coerce_text(fields.get("description"))),
        "idea_card_text": "",
        "generated_summary": clean_text(context.get("generated_summary")),
    }


def normalize_idea_card(context: dict[str, Any]) -> dict[str, str]:
    return {
        "summary": clean_text(context.get("summary")),
        "description": clean_text(context.get("description")),
        "idea_card_text": clean_text(context.get("idea_card_text")),
        "generated_summary": clean_text(context.get("generated_summary")),
    }


def load_gt_payload_if_available(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tickets": {}}
    return normalize_gt_payload(json.loads(path.read_text(encoding="utf-8")))


def normalize_gt_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"tickets": {}}
    tickets = payload.get("tickets")
    if isinstance(tickets, dict):
        return {
            **payload,
            "tickets": {
                normalize_ticket_key(ticket_id): row
                for ticket_id, row in tickets.items()
                if normalize_ticket_key(ticket_id)
            },
        }
    if isinstance(tickets, list):
        return {
            **payload,
            "tickets": {
                normalize_ticket_key(row.get("ticket_id") or row.get("idmt_key")): row
                for row in tickets
                if isinstance(row, dict)
                and normalize_ticket_key(row.get("ticket_id") or row.get("idmt_key"))
            },
        }
    return {"tickets": {}}


def ground_truth_for_ticket(gt_payload: dict[str, Any], ticket_id: str) -> tuple[dict[str, Any], bool]:
    ticket_key = normalize_ticket_key(ticket_id)
    row = (gt_payload.get("tickets") or {}).get(ticket_key)
    if not isinstance(row, dict):
        for key, value in (gt_payload.get("tickets") or {}).items():
            if normalize_ticket_key(key) == ticket_key and isinstance(value, dict):
                row = value
                break
    if not isinstance(row, dict):
        return empty_ground_truth(), False
    return normalize_ground_truth_ticket(row.get("ground_truth") if "ground_truth" in row else row), True


def normalize_ground_truth_ticket(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return empty_ground_truth()
    gt_by_value_stream = {
        clean_text(value_stream_name): clean_stage_names(stages)
        for value_stream_name, stages in (row.get("gt_by_value_stream") or {}).items()
        if clean_text(value_stream_name)
    }
    return {
        "gt_by_value_stream": gt_by_value_stream,
        "linked_themes": list(row.get("linked_themes") or []),
    }


def empty_ground_truth() -> dict[str, Any]:
    return {"gt_by_value_stream": {}, "linked_themes": []}


def clean_stage_names(values: Any) -> list[str]:
    rows = values if isinstance(values, list) else [values]
    return dedupe_text(clean_text(value) for value in rows if clean_text(value))


def has_prediction_context(idea_card: dict[str, Any]) -> bool:
    return any(
        clean_text(idea_card.get(key))
        for key in ("summary", "description", "idea_card_text", "generated_summary")
    )


def has_ground_truth(ground_truth: dict[str, Any]) -> bool:
    return bool(
        ground_truth.get("gt_by_value_stream")
        or ground_truth.get("linked_themes")
    )


def make_jira_client_if_configured() -> JiraApiClient | None:
    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    token = os.getenv("JIRA_TOKEN", "").strip()
    if not base_url or not token:
        return None
    return JiraApiClient(base_url=base_url, token=token, verify_ssl=False)


def write_dataset_outputs(dataset: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8")

    jsonl_path = output_path.with_suffix(".jsonl")
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ticket_id, ticket in (dataset.get("tickets") or {}).items():
            fh.write(json.dumps({"ticket_id": ticket_id, **ticket}, ensure_ascii=False) + "\n")


def print_dataset_summary(dataset: dict[str, Any], output_path: Path) -> None:
    tickets = list((dataset.get("tickets") or {}).values())
    print("Stage prediction dataset complete")
    print(f"Tickets processed: {len(tickets)}")
    print(f"Tickets with prediction context: {sum(1 for row in tickets if has_prediction_context(row.get('idea_card') or {}))}")
    print(f"Tickets with predicted VS: {sum(1 for row in tickets if row.get('predicted_value_streams'))}")
    print(f"Tickets with GT: {sum(1 for row in tickets if has_ground_truth(row.get('ground_truth') or {}))}")
    print(f"Errors: {len(dataset.get('errors') or [])}")
    print(f"Output path: {output_path}")


def dedupe_text(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
