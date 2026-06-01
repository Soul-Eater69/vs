"""Build the unified dataset for stage prediction and evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
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
from vs_app.ingestion.ground_truth.stage_support import classify_stage_support
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
    read_ticket_ids,
)


DEFAULT_TICKETS_INPUT = Path("output/theme_duplicate_scan/clean_ticket_ids.txt")
DEFAULT_GT_INPUT = Path("output/stage_eval/stage_ground_truth.json")
DEFAULT_OUTPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_GT_CONCURRENCY = 5
IDMT_CONTEXT_FIELDS = ["summary", "description", "attachment"]


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    config = load_runtime_config()
    ticket_ids = read_ticket_ids(config["tickets_input"])
    fallback_gt_payload = load_gt_payload_if_available(config["gt_input"])
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")
    jira_client = make_jira_client_if_configured()
    classify_support, support_llm_client, support_cfg = make_stage_support_classifier_inputs(config)

    if jira_client is not None:
        async with jira_client:
            dataset = await build_stage_prediction_dataset(
                ticket_ids=ticket_ids,
                fallback_gt_payload=fallback_gt_payload,
                stage_catalog=stage_catalog,
                jira_client=jira_client,
                config=config,
                classify_support=classify_support,
                support_llm_client=support_llm_client,
                support_cfg=support_cfg,
            )
    else:
        dataset = await build_stage_prediction_dataset(
            ticket_ids=ticket_ids,
            fallback_gt_payload=fallback_gt_payload,
            stage_catalog=stage_catalog,
            jira_client=None,
            config=config,
            classify_support=classify_support,
            support_llm_client=support_llm_client,
            support_cfg=support_cfg,
        )

    write_dataset_outputs(dataset, config["output"])
    print_dataset_summary(dataset, config["output"])
    return 0


def load_runtime_config() -> dict[str, Any]:
    return {
        "tickets_input": Path(os.getenv("STAGE_DATASET_TICKETS_INPUT", str(DEFAULT_TICKETS_INPUT))),
        "gt_input": Path(os.getenv("STAGE_DATASET_GT_INPUT", str(DEFAULT_GT_INPUT))),
        "output": Path(os.getenv("STAGE_DATASET_OUTPUT", str(DEFAULT_OUTPUT))),
        "stage_catalog": Path(os.getenv("STAGE_DATASET_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
        "concurrency": env_int(
            "STAGE_DATASET_CONCURRENCY",
            env_int("STAGE_GT_CONCURRENCY", DEFAULT_GT_CONCURRENCY),
        ),
        "classify_stage_support": env_flag("STAGE_DATASET_CLASSIFY_STAGE_SUPPORT"),
    }


def make_stage_support_classifier_inputs(
    config: dict[str, Any],
) -> tuple[bool, Any, Any]:
    """Return (enabled, llm_client, cfg) for optional stage support classification.

    An LLM client is constructed only when STAGE_DATASET_CLASSIFY_STAGE_SUPPORT is
    enabled, so default dataset generation creates no client and makes no LLM call.
    """
    if not config.get("classify_stage_support"):
        return False, None, None
    cfg = JiraIngestionConfig()
    from vs_app.integrations.clients.llm import IDPChatOpenAI

    return True, IDPChatOpenAI(model=cfg.llm_model), cfg


async def build_stage_prediction_dataset(
    *,
    ticket_ids: list[str],
    fallback_gt_payload: dict[str, Any],
    stage_catalog: dict[str, Any],
    jira_client: JiraApiClient | None,
    config: dict[str, Any],
    classify_support: bool = False,
    support_llm_client: Any = None,
    support_cfg: Any = None,
) -> dict[str, Any]:
    tickets: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    total = len(ticket_ids)
    concurrency = max(1, int(config.get("concurrency") or DEFAULT_GT_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)

    async def build_one(index: int, ticket_id: str) -> dict[str, Any]:
        ticket_key = normalize_ticket_key(ticket_id)
        async with semaphore:
            print(f"[{index}/{total}] START {ticket_key}", flush=True)
            try:
                row = await build_dataset_ticket(
                    ticket_id=ticket_key,
                    fallback_gt_payload=fallback_gt_payload,
                    stage_catalog=stage_catalog,
                    jira_client=jira_client,
                    classify_support=classify_support,
                    support_llm_client=support_llm_client,
                    support_cfg=support_cfg,
                )
                print_ticket_status_done(index, total, ticket_key, row)
                return {"ticket_id": ticket_key, "row": row, "error": None}
            except Exception as exc:
                error = {
                    "ticket_id": ticket_key,
                    "error": compact_error(exc),
                }
                print(
                    f"[{index}/{total}] ERROR {ticket_key} | {compact_error(exc)}",
                    flush=True,
                )
                return {"ticket_id": ticket_key, "row": None, "error": error}

    results = await asyncio.gather(
        *(
            build_one(index, ticket_id)
            for index, ticket_id in enumerate(ticket_ids, start=1)
        )
    )
    result_by_ticket = {
        normalize_ticket_key(result["ticket_id"]): result
        for result in results
        if normalize_ticket_key(result.get("ticket_id"))
    }

    for ticket_id in ticket_ids:
        ticket_key = normalize_ticket_key(ticket_id)
        result = result_by_ticket.get(ticket_key)
        if not result:
            continue
        if result.get("row") is not None:
            tickets[ticket_key] = result["row"]
        if result.get("error") is not None:
            errors.append(result["error"])

    print(
        f"Stage prediction dataset ticket build finished "
        f"(concurrency={concurrency}, tickets={total})",
        flush=True,
    )

    return {
        "source": "stage_prediction_dataset",
        "generated_at": utc_now(),
        "tickets_input": str(config["tickets_input"]),
        "gt_input": str(config["gt_input"]),
        "tickets": tickets,
        "errors": errors,
    }


def _stage_support_context_text(idea_card: dict[str, Any]) -> str:
    """Assemble the original IDMT packet for stage support classification.

    Uses only original-context fields; never any predicted/evaluation output.
    Identical fields are de-duplicated so the prompt is not padded with repeats.
    """
    parts = [
        idea_card.get("summary", ""),
        idea_card.get("description", ""),
        idea_card.get("idea_card_text", ""),
        idea_card.get("attachment_text", ""),
        idea_card.get("extracted_text", ""),
        idea_card.get("generated_summary", ""),
    ]
    seen: set[str] = set()
    blocks: list[str] = []
    for part in parts:
        text = clean_text(part)
        if text and text not in seen:
            seen.add(text)
            blocks.append(text)
    return "\n\n".join(blocks)


async def classify_stage_support_for_row(
    *,
    ticket_id: str,
    idea_card: dict[str, Any],
    ground_truth: dict[str, Any],
    llm_client: Any,
    cfg: Any,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Classify stage support for a dataset row's GT stages (answer-key evidence).

    Returns JSON-ready rows. Lenient: returns [] (and records a warning) on any
    failure so the dataset build never breaks; uncovered GT stages are backfilled
    as unknown/jira_gt by the document builder later. Never fed into prediction.
    """
    gt_by_value_stream = (ground_truth or {}).get("gt_by_value_stream") or {}
    if llm_client is None or not gt_by_value_stream:
        return []
    context_text = _stage_support_context_text(idea_card)
    if not context_text.strip():
        return []
    try:
        rows = await asyncio.to_thread(
            classify_stage_support,
            ticket_id=ticket_id,
            consolidated_text=context_text,
            gt_by_value_stream=gt_by_value_stream,
            llm_client=llm_client,
            cfg=cfg,
        )
    except Exception as exc:
        warnings.append(f"stage support classification failed: {compact_error(exc)}")
        return []
    return [asdict(row) for row in rows]


async def build_dataset_ticket(
    *,
    ticket_id: str,
    fallback_gt_payload: dict[str, Any],
    stage_catalog: dict[str, Any],
    jira_client: JiraApiClient | None,
    classify_support: bool = False,
    support_llm_client: Any = None,
    support_cfg: Any = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    idmt_issue = await fetch_idmt_context_issue(ticket_id, jira_client, warnings)
    idea_card = await idea_card_for_ticket(
        ticket_id=ticket_id,
        idmt_issue=idmt_issue,
        jira_client=jira_client,
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
    if not has_ground_truth(ground_truth):
        warnings.append("stage ground truth unavailable")

    row: dict[str, Any] = {
        "ticket_id": ticket_id,
        "idea_card": idea_card,
        "ground_truth": ground_truth,
        "warnings": dedupe_text(warnings),
    }
    if classify_support:
        row["stage_support"] = await classify_stage_support_for_row(
            ticket_id=ticket_id,
            idea_card=idea_card,
            ground_truth=ground_truth,
            llm_client=support_llm_client,
            cfg=support_cfg,
            warnings=warnings,
        )
        row["warnings"] = dedupe_text(warnings)
    return row


async def fetch_idmt_context_issue(
    ticket_id: str,
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> dict[str, Any]:
    if jira_client is None:
        warnings.append("Jira credentials unavailable; IDMT context not fetched")
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
    idmt_issue: dict[str, Any],
    jira_client: JiraApiClient | None,
    warnings: list[str],
) -> dict[str, str]:
    issue_context = context_from_idmt_issue(ticket_id, idmt_issue)
    consolidated_text = await consolidated_ticket_text(
        ticket_id=ticket_id,
        idmt_issue=idmt_issue,
        jira_client=jira_client,
        warnings=warnings,
    )
    attachment_text = document_sections_from_consolidated_text(consolidated_text)
    idea_card_text = first_text(
        consolidated_text,
        joined_context_text(issue_context),
    )
    generated_summary = first_text(
        condense_idea_card(idea_card_text, max_chars=3500) if idea_card_text else "",
    )

    return {
        "summary": clean_text(issue_context.get("summary")),
        "description": clean_text(issue_context.get("description")),
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
    gt_by_value_stream: dict[str, list[str]] = {}
    for value_stream_name, stages in (row.get("gt_by_value_stream") or {}).items():
        clean_name = clean_text(value_stream_name)
        clean_stages = clean_stage_names(stages)
        if clean_name and clean_stages:
            gt_by_value_stream[clean_name] = clean_stages
    return {"gt_by_value_stream": gt_by_value_stream}


def empty_ground_truth() -> dict[str, Any]:
    return {"gt_by_value_stream": {}}


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
    return bool(ground_truth.get("gt_by_value_stream"))


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
    print(f"Tickets with GT: {sum(1 for row in tickets if has_ground_truth(row.get('ground_truth') or {}))}")
    print(f"Errors: {len(dataset.get('errors') or [])}")
    print(f"Output path: {output_path}")


def print_ticket_status_done(index: int, total: int, ticket_id: str, row: dict[str, Any]) -> None:
    idea_card = row.get("idea_card") or {}
    gt = row.get("ground_truth") or {}
    gt_by_vs = gt.get("gt_by_value_stream") or {}
    gt_stage_count = sum(len(stages or []) for stages in gt_by_vs.values())
    context_ok = has_prediction_context(idea_card)
    print(
        f"[{index}/{total}] DONE {ticket_id} | "
        f"context={'yes' if context_ok else 'no'} | "
        f"gt_vs={len(gt_by_vs)} | "
        f"gt_stages={gt_stage_count} | "
        f"warnings={len(row.get('warnings') or [])}",
        flush=True,
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


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


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
