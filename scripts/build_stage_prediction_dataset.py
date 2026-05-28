"""Build the unified dataset for stage prediction and evaluation."""

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
from vs_app.ingestion.summary.text_consolidator import consolidate_ticket_text
from vs_app.jobs.jira_batch.config import JiraIngestionConfig
from vs_app.modules.rag.query.views import condense_idea_card
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    build_ticket_stage_ground_truth,
    normalize_ticket_key,
)
from vs_app.modules.stages.stage_prediction_io import (
    clean_text,
    coerce_text,
    context_from_prediction_record,
    load_value_stream_predictions,
    normalized_value_stream,
    predicted_value_streams_for_ticket,
    read_ticket_ids,
    value_stream_prediction_record,
)


DEFAULT_TICKETS_INPUT = Path("output/theme_duplicate_scan/clean_ticket_ids.txt")
DEFAULT_VS_INPUT = Path("output/value_stream_predictions.json")
DEFAULT_GT_INPUT = Path("output/stage_eval/stage_ground_truth.json")
DEFAULT_OUTPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
IDMT_CONTEXT_FIELDS = ["summary", "description", "attachment"]


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    config = load_runtime_config()
    ticket_ids = read_ticket_ids(config["tickets_input"])
    vs_predictions = load_optional_value_stream_predictions(config["vs_input"])
    fallback_gt_payload = load_gt_payload_if_available(config["gt_input"])
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")
    jira_client = make_jira_client_if_configured()

    if jira_client is not None:
        async with jira_client:
            dataset = await build_stage_prediction_dataset(
                ticket_ids=ticket_ids,
                vs_predictions=vs_predictions,
                fallback_gt_payload=fallback_gt_payload,
                stage_catalog=stage_catalog,
                jira_client=jira_client,
                config=config,
            )
    else:
        dataset = await build_stage_prediction_dataset(
            ticket_ids=ticket_ids,
            vs_predictions=vs_predictions,
            fallback_gt_payload=fallback_gt_payload,
            stage_catalog=stage_catalog,
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
    fallback_gt_payload: dict[str, Any],
    stage_catalog: dict[str, Any],
    jira_client: JiraApiClient | None,
    config: dict[str, Path],
) -> dict[str, Any]:
    tickets: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []

    for ticket_id in ticket_ids:
        ticket_key = normalize_ticket_key(ticket_id)
        try:
            tickets[ticket_key] = await build_dataset_ticket(
                ticket_id=ticket_key,
                vs_predictions=vs_predictions,
                fallback_gt_payload=fallback_gt_payload,
                stage_catalog=stage_catalog,
                jira_client=jira_client,
            )
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
    fallback_gt_payload: dict[str, Any],
    stage_catalog: dict[str, Any],
    jira_client: JiraApiClient | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    prediction_record = value_stream_prediction_record(vs_predictions, ticket_id)
    idmt_issue = await fetch_idmt_context_issue(ticket_id, jira_client, warnings)
    idea_card = await idea_card_for_ticket(
        ticket_id=ticket_id,
        prediction_record=prediction_record,
        idmt_issue=idmt_issue,
        jira_client=jira_client,
        warnings=warnings,
    )
    predicted_value_streams = await predicted_value_streams_for_dataset_ticket(
        ticket_id=ticket_id,
        vs_predictions=vs_predictions,
        idea_card=idea_card,
        warnings=warnings,
    )
    ground_truth = await stage_ground_truth_for_dataset_ticket(
        ticket_id=ticket_id,
        jira_client=jira_client,
        stage_catalog=stage_catalog,
        fallback_gt_payload=fallback_gt_payload,
        warnings=warnings,
    )

    if not has_prediction_context(idea_card):
        warnings.append("IDMT idea-card context missing")
    if not predicted_value_streams:
        warnings.append("predicted Value Streams missing")
    if not has_ground_truth(ground_truth):
        warnings.append("stage ground truth unavailable")

    return {
        "ticket_id": ticket_id,
        "idea_card": idea_card,
        "predicted_value_streams": predicted_value_streams,
        "ground_truth": ground_truth,
        "warnings": dedupe_text(warnings),
    }


async def fetch_idmt_context_issue(
    ticket_id: str,
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> dict[str, Any]:
    if jira_client is None:
        warnings.append("Jira credentials unavailable; IDMT context fetched only from VS cache")
        return {}
    try:
        return await jira_client.get_issue(
            ticket_id,
            fields=IDMT_CONTEXT_FIELDS,
            expand=False,
        )
    except Exception as exc:
        warnings.append(f"IDMT context fetch failed: {compact_error(exc)}")
        return {}


async def idea_card_for_ticket(
    *,
    ticket_id: str,
    prediction_record: dict[str, Any],
    idmt_issue: dict[str, Any],
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> dict[str, str]:
    record_context = context_from_prediction_record(ticket_id, prediction_record)
    issue_context = context_from_idmt_issue(ticket_id, idmt_issue)
    consolidated_text = await consolidated_ticket_text(
        ticket_id=ticket_id,
        idmt_issue=idmt_issue,
        jira_client=jira_client,
        warnings=warnings,
    )
    attachment_text = document_sections_from_consolidated_text(consolidated_text)
    idea_card_text = first_text(
        record_context.get("idea_card_text"),
        consolidated_text,
        joined_context_text(issue_context),
        joined_context_text(record_context),
    )
    generated_summary = first_text(
        record_context.get("generated_summary"),
        generated_summary_from_prediction_record(prediction_record),
        condense_idea_card(idea_card_text, max_chars=3500) if idea_card_text else "",
    )

    return {
        "summary": first_text(record_context.get("summary"), issue_context.get("summary")),
        "description": first_text(record_context.get("description"), issue_context.get("description")),
        "idea_card_text": clean_text(idea_card_text),
        "attachment_text": clean_text(attachment_text),
        "extracted_text": clean_text(consolidated_text),
        "generated_summary": clean_text(generated_summary),
    }


def context_from_idmt_issue(ticket_id: str, issue: dict[str, Any]) -> dict[str, str]:
    fields = issue.get("fields") or {}
    return {
        "ticket_id": ticket_id,
        "summary": clean_text(fields.get("summary")),
        "description": clean_text(coerce_text(fields.get("description"))),
        "idea_card_text": "",
        "generated_summary": "",
    }


async def consolidated_ticket_text(
    *,
    ticket_id: str,
    idmt_issue: dict[str, Any],
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> str:
    if not idmt_issue or jira_client is None:
        return ""
    try:
        return clean_text(
            await consolidate_ticket_text(
                idmt_issue,
                jira_client,
                JiraIngestionConfig(),
            )
        )
    except Exception as exc:
        warnings.append(f"IDMT attachment/text consolidation failed: {compact_error(exc)}")
        fields = idmt_issue.get("fields") or {}
        return clean_text(
            "\n\n".join(
                part
                for part in (
                    fields.get("summary"),
                    coerce_text(fields.get("description")),
                )
                if clean_text(part)
            )
        )


async def predicted_value_streams_for_dataset_ticket(
    *,
    ticket_id: str,
    vs_predictions: dict[str, Any],
    idea_card: dict[str, Any],
    warnings: list[str],
) -> list[dict[str, Any]]:
    cached = predicted_value_streams_for_ticket(
        vs_predictions=vs_predictions,
        ticket_id=ticket_id,
    )
    if cached:
        return cached

    query = first_text(
        idea_card.get("generated_summary"),
        idea_card.get("idea_card_text"),
        idea_card.get("extracted_text"),
        joined_context_text(idea_card),
    )
    if not query:
        warnings.append("cannot run VS prediction; idea-card context is empty")
        return []

    try:
        return await run_value_stream_prediction(ticket_id=ticket_id, idea_card_text=query)
    except Exception as exc:
        warnings.append(f"VS prediction pipeline failed: {compact_error(exc)}")
        return []


async def run_value_stream_prediction(
    *,
    ticket_id: str,
    idea_card_text: str,
) -> list[dict[str, Any]]:
    from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService

    service = ValueStreamRagService()
    result = await service.predict(
        ValueStreamRagCommand(
            ticket_id=ticket_id,
            idea_card_text=idea_card_text,
            exclude_source_ticket_from_historical=True,
        )
    )
    return [
        normalized_value_stream(row)
        for row in result.predicted_value_streams
        if normalized_value_stream(row).get("name")
    ]


async def stage_ground_truth_for_dataset_ticket(
    *,
    ticket_id: str,
    jira_client: JiraApiClient | None,
    stage_catalog: dict[str, Any],
    fallback_gt_payload: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    if jira_client is not None:
        try:
            return normalize_ground_truth_ticket(
                await build_ticket_stage_ground_truth(
                    ticket_key=ticket_id,
                    jira_client=jira_client,
                    catalog=stage_catalog,
                )
            )
        except Exception as exc:
            warnings.append(f"stage GT build failed: {compact_error(exc)}")

    fallback, found = ground_truth_for_ticket(fallback_gt_payload, ticket_id)
    if found:
        if jira_client is None:
            warnings.append("stage GT loaded from fallback file because Jira is unavailable")
        else:
            warnings.append("stage GT loaded from fallback file after build failure")
        return fallback

    if jira_client is None:
        warnings.append("stage GT not built because Jira credentials are unavailable")
    return empty_ground_truth()


def load_optional_value_stream_predictions(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tickets": {}}
    return load_value_stream_predictions(path)


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
        for key in (
            "summary",
            "description",
            "idea_card_text",
            "attachment_text",
            "extracted_text",
            "generated_summary",
        )
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
    print(
        "Tickets with prediction context: "
        f"{sum(1 for row in tickets if has_prediction_context(row.get('idea_card') or {}))}"
    )
    print(f"Tickets with predicted VS: {sum(1 for row in tickets if row.get('predicted_value_streams'))}")
    print(f"Tickets with GT: {sum(1 for row in tickets if has_ground_truth(row.get('ground_truth') or {}))}")
    print(f"Errors: {len(dataset.get('errors') or [])}")
    print(f"Output path: {output_path}")


def generated_summary_from_prediction_record(record: dict[str, Any]) -> str:
    query_preparation = (
        record.get("query_preparation")
        if isinstance(record.get("query_preparation"), dict)
        else {}
    )
    debug = record.get("debug") if isinstance(record.get("debug"), dict) else {}
    return first_text(
        query_preparation.get("query_for_prompt"),
        query_preparation.get("cleaned_query"),
        debug.get("query_for_prompt"),
        debug.get("condensed_idea_card"),
    )


def document_sections_from_consolidated_text(value: str) -> str:
    text = str(value or "")
    if "[DOCUMENT:" not in text:
        return ""
    return clean_text("[DOCUMENT:" + text.split("[DOCUMENT:", 1)[1])


def joined_context_text(context: dict[str, Any]) -> str:
    return clean_text(
        "\n\n".join(
            clean_text(context.get(key))
            for key in ("summary", "description", "idea_card_text", "extracted_text")
            if clean_text(context.get(key))
        )
    )


def first_text(*values: Any) -> str:
    return next((clean_text(value) for value in values if clean_text(value)), "")


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
