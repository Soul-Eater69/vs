from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from ....ingestion.adapters.jira import JiraTicketClient
from ....config import JIRA_BASE_URL, JIRA_TOKEN

DEFAULT_OUTPUT = Path("historical_filter_value_stream_audit.json")
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_CONCURRENT = 8
VERIFY_SSL = False


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False, default=str)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ticket_id",
        "title",
        "status",
        "value_stream_count",
        "has_value_streams",
        "value_stream_names",
        "value_stream_ids",
        "value_stream_statuses",
        "value_stream_label_source",
    ]
    with path.open("w", encoding="utf-8", newline="") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _extract_filter_id(filter_id: Optional[str], filter_url: Optional[str]) -> Optional[str]:
    if filter_id:
        return str(filter_id).strip()
    if not filter_url:
        return None

    parsed = urlparse(filter_url)
    query_filter = parse_qs(parsed.query).get("filter", [])
    if query_filter:
        return query_filter[0].strip()

    parts = [part for part in parsed.path.split("/") if part]
    if "filters" in parts:
        index = parts.index("filters")
        if index + 1 < len(parts):
            return parts[index + 1].strip()

    return None


async def _resolve_jql(
    jira_client: JiraTicketClient,
    *,
    jql: Optional[str],
    filter_id: Optional[str],
    filter_url: Optional[str],
) -> tuple[str, dict[str, Any]]:
    if jql and jql.strip():
        return jql.strip(), {"source": "jql", "filter_id": None, "filter_name": None}

    resolved_filter_id = _extract_filter_id(filter_id, filter_url)
    if not resolved_filter_id:
        raise ValueError("Provide either --jql or --filter-id/--filter-url")

    filter_data = await jira_client.get_filter(resolved_filter_id)
    resolved_jql = str(filter_data.get("jql") or "").strip()
    if not resolved_jql:
        raise RuntimeError(f"Filter {resolved_filter_id} did not return JQL")

    return resolved_jql, {
        "source": "saved_filter",
        "filter_id": resolved_filter_id,
        "filter_name": str(filter_data.get("name") or ""),
    }


async def _fetch_ticket_keys(
    jira_client: JiraTicketClient,
    *,
    jql: str,
    page_size: int,
    limit: Optional[int],
) -> tuple[list[str], Optional[int]]:
    ticket_keys: list[str] = []
    start_at = 0
    total: Optional[int] = None

    while True:
        page = await jira_client.search_issues(
            jql,
            start_at=start_at,
            max_results=page_size,
        )
        issues = page.get("issues", []) or []
        total = page.get("total", total)

        if not issues:
            break

        for issue in issues:
            key = str(issue.get("key") or "").strip()
            if key:
                ticket_keys.append(key)
                if limit is not None and len(ticket_keys) >= limit:
                    return ticket_keys, total

        start_at += len(issues)
        if total is not None and start_at >= total:
            break

    return ticket_keys, total


def _build_audit_row(ticket_data: dict[str, Any]) -> dict[str, Any]:
    fields = ticket_data.get("fields", {}) or {}
    value_stream_names = [str(name).strip() for name in (ticket_data.get("value_stream_names") or []) if str(name).strip()]
    value_stream_ids = [str(value).strip() for value in (ticket_data.get("value_stream_ids") or []) if str(value).strip()]
    value_stream_statuses = [str(value).strip() for value in (ticket_data.get("value_stream_statuses") or []) if str(value).strip()]

    return {
        "ticket_id": str(ticket_data.get("key") or ""),
        "title": str(fields.get("summary") or ""),
        "status": str((fields.get("status") or {}).get("name") or ""),
        "value_stream_count": len(value_stream_names),
        "has_value_streams": bool(value_stream_names),
        "value_stream_names": "|".join(value_stream_names),
        "value_stream_ids": "|".join(value_stream_ids),
        "value_stream_statuses": "|".join(value_stream_statuses),
        "value_stream_label_source": str(ticket_data.get("value_stream_label_source") or ""),
    }


async def _audit_ticket_set(
    jira_client: JiraTicketClient,
    ticket_keys: list[str],
    *,
    max_concurrent: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _audit_one(ticket_key: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
        async with semaphore:
            try:
                ticket_data = await jira_client.get_ticket_data(ticket_key)
                return _build_audit_row(ticket_data), None
            except Exception as exc:
                return None, {"ticket_id": ticket_key, "error": str(exc)}

    results = await asyncio.gather(*[_audit_one(ticket_key) for ticket_key in ticket_keys])
    for row, error in results:
        if row is not None:
            rows.append(row)
        if error is not None:
            errors.append(error)

    rows.sort(key=lambda row: row["ticket_id"])
    errors.sort(key=lambda row: row["ticket_id"])
    return rows, errors


async def run_filter_value_stream_audit(
    *,
    jql: Optional[str],
    filter_id: Optional[str],
    filter_url: Optional[str],
    output_path: Path,
    page_size: int,
    limit: Optional[int],
    max_concurrent: int,
) -> dict[str, Any]:
    if not JIRA_BASE_URL or not JIRA_TOKEN:
        raise RuntimeError("JIRA_BASE_URL and JIRA_TOKEN must be set")

    async with JiraTicketClient(
        base_url=JIRA_BASE_URL,
        token=JIRA_TOKEN,
        verify_ssl=VERIFY_SSL,
    ) as jira_client:
        resolved_jql, filter_meta = await _resolve_jql(
            jira_client,
            jql=jql,
            filter_id=filter_id,
            filter_url=filter_url,
        )
        ticket_keys, total = await _fetch_ticket_keys(
            jira_client,
            jql=resolved_jql,
            page_size=page_size,
            limit=limit,
        )
        rows, errors = await _audit_ticket_set(
            jira_client,
            ticket_keys,
            max_concurrent=max_concurrent,
        )

    tickets_with_value_streams = sum(1 for row in rows if row["has_value_streams"])
    tickets_without_value_streams = sum(1 for row in rows if not row["has_value_streams"])
    payload = {
        "query": {
            "jql": resolved_jql,
            **filter_meta,
        },
        "summary": {
            "search_total": total,
            "tickets_selected": len(ticket_keys),
            "tickets_audited": len(rows),
            "tickets_with_value_streams": tickets_with_value_streams,
            "tickets_without_value_streams": tickets_without_value_streams,
            "tickets_failed": len(errors),
        },
        "tickets": rows,
        "errors": errors,
    }

    dump_json(payload, output_path)
    write_csv(rows, output_path.with_suffix(".csv"))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-off analysis script: fetch Jira tickets from a filter or JQL and audit value-stream coverage."
    )
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument("--jql", help="JQL query to search")
    query_group.add_argument("--filter-id", help="Saved Jira filter ID")
    query_group.add_argument("--filter-url", help="Saved Jira filter URL")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"JSON output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Search page size (default: {DEFAULT_PAGE_SIZE})",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on number of tickets to audit")
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=DEFAULT_MAX_CONCURRENT,
        help=f"Concurrent ticket fetches during audit (default: {DEFAULT_MAX_CONCURRENT})",
    )
    args = parser.parse_args()

    payload = asyncio.run(
        run_filter_value_stream_audit(
            jql=args.jql,
            filter_id=args.filter_id,
            filter_url=args.filter_url,
            output_path=args.output,
            page_size=args.page_size,
            limit=args.limit,
            max_concurrent=args.max_concurrent,
        )
    )

    summary = payload["summary"]
    print("-" * 80)
    print("VALUE STREAM AUDIT COMPLETE")
    print(f"Selected:             {summary['tickets_selected']}")
    print(f"Audited:              {summary['tickets_audited']}")
    print(f"With value streams:   {summary['tickets_with_value_streams']}")
    print(f"Without value streams:{summary['tickets_without_value_streams']}")
    print(f"Failed:               {summary['tickets_failed']}")
    print(f"JSON report:          {args.output}")
    print(f"CSV report:           {args.output.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
