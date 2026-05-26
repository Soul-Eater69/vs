"""Run offline value-stream stage evaluation."""

from __future__ import annotations

import argparse
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
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from build_stage_ground_truth import JiraApiClient, load_ticket_keys
from vs_app.modules.stages.stage_catalog import get_allowed_stages, load_stage_catalog
from vs_app.modules.stages.stage_metrics import (
    duplicate_raw_mentions_collapsed_count,
    evaluate_stage_predictions,
    unresolved_gt_stage_count,
    write_stage_eval_outputs,
)
from vs_app.modules.stages.stage_selector import predict_value_stream_stages


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    args = parse_args()
    if args.vs_source != "ground_truth":
        raise SystemExit("--vs-source=predicted is not implemented yet; use ground_truth.")

    ground_truth = json.loads(Path(args.stage_ground_truth).read_text(encoding="utf-8"))
    catalog = load_stage_catalog(
        path=args.stage_catalog,
        source=args.stage_catalog_source,
    )
    ticket_ids = load_ticket_keys(args) or sorted((ground_truth.get("tickets") or {}).keys())
    llm = None if args.no_llm else _make_generation_service()

    jira_client: JiraApiClient | None = None
    if args.jira_base_url and args.jira_token:
        jira_client = JiraApiClient(
            base_url=args.jira_base_url,
            token=args.jira_token,
            verify_ssl=bool(args.verify_ssl),
        )

    rows: list[dict[str, Any]] = []
    if jira_client is not None:
        async with jira_client:
            for ticket_id in ticket_ids:
                rows.extend(await _evaluate_ticket(ticket_id, ground_truth, catalog, llm, jira_client, args))
    else:
        for ticket_id in ticket_ids:
            rows.extend(await _evaluate_ticket(ticket_id, ground_truth, catalog, llm, None, args))

    result = evaluate_stage_predictions(
        rows,
        unresolved_gt_stage_count=unresolved_gt_stage_count(ground_truth),
        duplicate_raw_mentions_collapsed_count=duplicate_raw_mentions_collapsed_count(ground_truth),
    )
    result["metadata"] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stage_catalog_source": args.stage_catalog_source,
        "vs_source": args.vs_source,
        "include_unverified_gt": bool(args.include_unverified_gt),
    }
    write_stage_eval_outputs(result, args.output_dir)
    print(f"Wrote stage evaluation outputs to {args.output_dir}")
    print(
        "micro P/R/F1 = "
        f"{result['summary']['micro_precision']:.3f} / "
        f"{result['summary']['micro_recall']:.3f} / "
        f"{result['summary']['micro_f1']:.3f}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate value-stream stage prediction.")
    parser.add_argument("--ticket-id", action="append", default=[], help="Ticket key. Can be repeated.")
    parser.add_argument("--ticket", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--ticket-ids-file", "--tickets-file", help="Text, JSON, or CSV ticket list.")
    parser.add_argument(
        "--stage-ground-truth",
        required=True,
        help="Path to stage_ground_truth.json.",
    )
    parser.add_argument("--stage-catalog", help="Path to JSON stage catalog.")
    parser.add_argument(
        "--stage-catalog-source",
        choices=["json", "azure"],
        default="json",
        help="Approved stage catalog source.",
    )
    parser.add_argument(
        "--vs-source",
        choices=["ground_truth", "predicted"],
        default="ground_truth",
        help="Value streams to evaluate. Only ground_truth is implemented.",
    )
    parser.add_argument(
        "--include-unverified-gt",
        action="store_true",
        help="Include unresolved raw GT stage mentions as GT labels.",
    )
    parser.add_argument(
        "--output-dir",
        default="output/stage_eval/run_gt_vs",
        help="Output directory for stage_eval.json/jsonl/csv.",
    )
    parser.add_argument("--jira-base-url", default=os.getenv("JIRA_BASE_URL"))
    parser.add_argument("--jira-token", default=os.getenv("JIRA_TOKEN"))
    parser.add_argument("--verify-ssl", action="store_true")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM prediction and emit empty stage predictions.",
    )
    return parser.parse_args()


async def _evaluate_ticket(
    ticket_id: str,
    ground_truth: dict[str, Any],
    catalog: dict,
    llm: Any | None,
    jira_client: JiraApiClient | None,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    ticket = (ground_truth.get("tickets") or {}).get(ticket_id)
    if not ticket:
        return []

    idea_card_text = await _idea_card_text(ticket_id, ticket, jira_client)
    rows: list[dict[str, Any]] = []
    for value_stream_name, gt_stages in (ticket.get("gt_by_value_stream") or {}).items():
        allowed = get_allowed_stages(value_stream_name, catalog)
        prediction = predict_value_stream_stages(
            idea_card_text=idea_card_text,
            value_stream_name=value_stream_name,
            allowed_stages=allowed,
            llm=llm,
        )
        predicted_stages = [
            str(row.get("stage") or "").strip()
            for row in prediction.get("predicted_stages") or []
            if str(row.get("stage") or "").strip()
        ]
        gt = list(gt_stages or [])
        if args.include_unverified_gt:
            gt.extend(_unresolved_for_value_stream(ticket, value_stream_name))
        rows.append(
            {
                "ticket_id": ticket_id,
                "value_stream_name": value_stream_name,
                "gt_stages": gt,
                "predicted_stages": predicted_stages,
                "prediction": prediction,
            }
        )
    return rows


async def _idea_card_text(
    ticket_id: str,
    ticket: dict[str, Any],
    jira_client: JiraApiClient | None,
) -> str:
    if jira_client is not None:
        try:
            issue = await jira_client.get_issue(
                ticket_id,
                fields=["summary", "description"],
                expand=False,
            )
            fields = issue.get("fields") or {}
            text = "\n\n".join(
                part
                for part in (
                    str(fields.get("summary") or ""),
                    _coerce_text(fields.get("description")),
                )
                if part.strip()
            )
            if text.strip():
                return text
        except Exception:
            pass

    parts = [
        str(ticket.get("idmt_summary") or ""),
        str(ticket.get("idmt_description") or ""),
    ]
    return "\n\n".join(part for part in parts if part.strip())


def _unresolved_for_value_stream(ticket: dict[str, Any], value_stream_name: str) -> list[str]:
    out: list[str] = []
    for theme in ticket.get("linked_themes") or []:
        if (theme.get("business_value_stream") or {}).get("name") != value_stream_name:
            continue
        for mention in theme.get("unresolved_stage_mentions") or []:
            raw = str(mention.get("raw_stage") or mention.get("raw") or "").strip()
            if raw:
                out.append(raw)
    return out


def _make_generation_service() -> Any | None:
    try:
        from vs_app.integrations.clients.generation_service import GenerationService

        return GenerationService()
    except Exception:
        return None


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "content" in value:
            return " ".join(_adf_text(value))
        for key in ("value", "text", "name"):
            if value.get(key):
                return str(value.get(key))
        return " ".join(_coerce_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_coerce_text(item) for item in value)
    return str(value)


def _adf_text(node: Any) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        if node.get("type") == "text" and node.get("text"):
            out.append(str(node.get("text")))
        for child in node.get("content") or []:
            out.extend(_adf_text(child))
        return out
    if isinstance(node, list):
        out: list[str] = []
        for item in node:
            out.extend(_adf_text(item))
        return out
    return []


if __name__ == "__main__":
    raise SystemExit(main())
