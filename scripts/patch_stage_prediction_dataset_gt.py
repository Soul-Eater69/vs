"""Patch stage_prediction_dataset.json ground_truth only, preserving idea-card context."""

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
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    build_ticket_stage_ground_truth,
    normalize_ticket_key,
)
from vs_app.modules.stages.stage_prediction_io import clean_text


DEFAULT_INPUT = Path("output/stage_prediction_eval/stage_prediction_dataset.json")
DEFAULT_OUTPUT = Path("output/stage_prediction_eval/stage_prediction_dataset_gt_patched.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_CONCURRENCY = 5
DEBUG_FILENAME = "stage_prediction_dataset_gt_debug.json"


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    config = load_runtime_config()
    dataset = load_json(config["input"])
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")

    base_url = os.getenv("JIRA_BASE_URL", "").strip()
    token = os.getenv("JIRA_TOKEN", "").strip()
    if not base_url or not token:
        raise SystemExit("GT patching requires JIRA_BASE_URL and JIRA_TOKEN.")

    async with JiraApiClient(base_url=base_url, token=token, verify_ssl=False) as jira_client:
        patched, debug_payload = await patch_dataset_ground_truth(
            dataset=dataset,
            jira_client=jira_client,
            stage_catalog=stage_catalog,
            config=config,
        )

    write_outputs(
        patched,
        debug_payload,
        output_path=config["output"],
        debug_path=config["debug_output"],
    )
    print_patch_summary(patched, config["output"], config["debug_output"])
    return 0


def load_runtime_config() -> dict[str, Any]:
    output = Path(os.getenv("STAGE_GT_PATCH_OUTPUT", str(DEFAULT_OUTPUT)))
    return {
        "input": Path(os.getenv("STAGE_GT_PATCH_INPUT", str(DEFAULT_INPUT))),
        "output": output,
        "debug_output": output.parent / DEBUG_FILENAME,
        "stage_catalog": Path(os.getenv("STAGE_DATASET_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
        "concurrency": env_int("STAGE_GT_PATCH_CONCURRENCY", DEFAULT_CONCURRENCY),
    }


async def patch_dataset_ground_truth(
    *,
    dataset: dict[str, Any],
    jira_client: JiraApiClient,
    stage_catalog: dict[str, Any],
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    ticket_ids = ticket_ids_from_dataset(dataset)
    total = len(ticket_ids)
    concurrency = max(1, int(config.get("concurrency") or DEFAULT_CONCURRENCY))
    semaphore = asyncio.Semaphore(concurrency)

    async def patch_one(index: int, ticket_id: str) -> dict[str, Any]:
        ticket_key = normalize_ticket_key(ticket_id)
        async with semaphore:
            print(f"[{index}/{total}] START {ticket_key}", flush=True)
            try:
                raw_gt = await build_ticket_stage_ground_truth(
                    ticket_key=ticket_key,
                    jira_client=jira_client,
                    catalog=stage_catalog,
                )
                compact_gt = compact_ground_truth(raw_gt)
                row = patched_ticket_row(
                    original_row=dataset_ticket_row(dataset, ticket_key),
                    compact_gt=compact_gt,
                    raw_gt=raw_gt,
                )
                print_patch_ticket_status_done(index, total, ticket_key, row)
                return {
                    "ticket_id": ticket_key,
                    "row": row,
                    "debug": debug_ticket_payload(raw_gt),
                    "error": None,
                }
            except Exception as exc:
                error = {"ticket_id": ticket_key, "error": compact_error(exc)}
                print(f"[{index}/{total}] ERROR {ticket_key} | {compact_error(exc)}", flush=True)
                fallback_row = compact_existing_ticket_row(dataset_ticket_row(dataset, ticket_key))
                return {"ticket_id": ticket_key, "row": fallback_row, "debug": None, "error": error}

    results = await asyncio.gather(
        *(
            patch_one(index, ticket_id)
            for index, ticket_id in enumerate(ticket_ids, start=1)
        )
    )
    result_by_ticket = {
        normalize_ticket_key(result["ticket_id"]): result
        for result in results
        if normalize_ticket_key(result.get("ticket_id"))
    }

    patched_tickets: dict[str, Any] = {}
    debug_tickets: dict[str, Any] = {}
    errors = list(dataset.get("errors") or [])
    for ticket_id in ticket_ids:
        ticket_key = normalize_ticket_key(ticket_id)
        result = result_by_ticket.get(ticket_key)
        if not result:
            continue
        patched_tickets[ticket_key] = result.get("row") or compact_existing_ticket_row(
            dataset_ticket_row(dataset, ticket_key)
        )
        if result.get("debug") is not None:
            debug_tickets[ticket_key] = result["debug"]
        if result.get("error") is not None:
            errors.append(result["error"])

    patched = dict(dataset)
    patched["tickets"] = patched_tickets
    patched["errors"] = errors
    patched["gt_patched_at"] = utc_now()
    patched["gt_patch_input"] = str(config["input"])
    patched["gt_patch_stage_catalog"] = str(config["stage_catalog"])

    debug_payload = {
        "source": "stage_prediction_dataset_gt_patch_debug",
        "generated_at": utc_now(),
        "input": str(config["input"]),
        "output": str(config["output"]),
        "tickets": debug_tickets,
        "errors": [error for error in errors if isinstance(error, dict) and error.get("ticket_id")],
    }
    return patched, debug_payload


def patched_ticket_row(
    *,
    original_row: dict[str, Any],
    compact_gt: dict[str, Any],
    raw_gt: dict[str, Any],
) -> dict[str, Any]:
    row = {
        "ticket_id": normalize_ticket_key(
            original_row.get("ticket_id") or raw_gt.get("idmt_key")
        ),
        "idea_card": original_row.get("idea_card") or {},
        "ground_truth": compact_gt,
    }
    row["warnings"] = updated_warnings(
        original_warnings=list(original_row.get("warnings") or []),
        raw_gt=raw_gt,
        compact_gt=compact_gt,
    )
    return row


def compact_existing_ticket_row(original_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticket_id": normalize_ticket_key(original_row.get("ticket_id")),
        "idea_card": original_row.get("idea_card") or {},
        "ground_truth": compact_ground_truth(original_row.get("ground_truth") or {}),
        "warnings": list(original_row.get("warnings") or []),
    }


def compact_ground_truth(raw_gt: dict[str, Any]) -> dict[str, Any]:
    gt_by_value_stream: dict[str, list[str]] = {}
    for value_stream_name, stages in (raw_gt.get("gt_by_value_stream") or {}).items():
        clean_name = clean_text(value_stream_name)
        clean_stages = clean_stage_names(stages)
        if clean_name and clean_stages:
            gt_by_value_stream[clean_name] = clean_stages
    return {"gt_by_value_stream": gt_by_value_stream}


def debug_ticket_payload(raw_gt: dict[str, Any]) -> dict[str, Any]:
    return {
        "linked_themes": list(raw_gt.get("linked_themes") or []),
        "warnings": list(raw_gt.get("warnings") or []),
        "raw_gt": raw_gt,
    }


def updated_warnings(
    *,
    original_warnings: list[Any],
    raw_gt: dict[str, Any],
    compact_gt: dict[str, Any],
) -> list[str]:
    warnings = [
        warning
        for warning in (clean_text(value) for value in original_warnings)
        if warning and not is_stage_gt_warning(warning)
    ]
    warnings.extend(clean_text(warning) for warning in (raw_gt.get("warnings") or []))
    if not compact_gt.get("gt_by_value_stream"):
        warnings.append("stage ground truth unavailable")
    return dedupe_text(warnings)


def is_stage_gt_warning(value: str) -> bool:
    text = clean_text(value).lower()
    return any(
        token in text
        for token in (
            "stage gt",
            "stage ground truth",
            "ground truth",
            "parent link",
            "child epic",
            "approved stage catalog",
        )
    )


def ticket_ids_from_dataset(dataset: dict[str, Any]) -> list[str]:
    return [
        normalize_ticket_key(ticket_id)
        for ticket_id in (dataset.get("tickets") or {}).keys()
        if normalize_ticket_key(ticket_id)
    ]


def dataset_ticket_row(dataset: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    ticket_key = normalize_ticket_key(ticket_id)
    direct = (dataset.get("tickets") or {}).get(ticket_key)
    if isinstance(direct, dict):
        return direct
    for key, row in (dataset.get("tickets") or {}).items():
        if normalize_ticket_key(key) == ticket_key and isinstance(row, dict):
            return row
    return {"ticket_id": ticket_key, "idea_card": {}, "ground_truth": {}, "warnings": []}


def print_patch_ticket_status_done(
    index: int,
    total: int,
    ticket_id: str,
    row: dict[str, Any],
) -> None:
    gt_by_vs = ((row.get("ground_truth") or {}).get("gt_by_value_stream") or {})
    gt_stage_count = sum(len(stages or []) for stages in gt_by_vs.values())
    print(
        f"[{index}/{total}] DONE {ticket_id} | "
        f"gt_vs={len(gt_by_vs)} | "
        f"gt_stages={gt_stage_count} | "
        f"warnings={len(row.get('warnings') or [])}",
        flush=True,
    )


def write_outputs(
    dataset: dict[str, Any],
    debug_payload: dict[str, Any],
    *,
    output_path: Path,
    debug_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False), encoding="utf-8")

    jsonl_path = output_path.with_suffix(".jsonl")
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for ticket_id, ticket in (dataset.get("tickets") or {}).items():
            fh.write(json.dumps({"ticket_id": ticket_id, **ticket}, ensure_ascii=False) + "\n")

    debug_path.write_text(json.dumps(debug_payload, indent=2, ensure_ascii=False), encoding="utf-8")


def print_patch_summary(dataset: dict[str, Any], output_path: Path, debug_path: Path) -> None:
    tickets = list((dataset.get("tickets") or {}).values())
    print("Stage prediction dataset GT patch complete")
    print(f"Tickets processed: {len(tickets)}")
    print(
        "Tickets with GT: "
        f"{sum(1 for row in tickets if (row.get('ground_truth') or {}).get('gt_by_value_stream'))}"
    )
    print(f"Errors: {len(dataset.get('errors') or [])}")
    print(f"Output path: {output_path}")
    print(f"Debug path: {debug_path}")


def load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def clean_stage_names(values: Any) -> list[str]:
    rows = values if isinstance(values, list) else [values]
    return dedupe_text(clean_text(value) for value in rows if clean_text(value))


def dedupe_text(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
