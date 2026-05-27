"""Scan IDMT tickets for duplicate linked Theme value streams.

The scanner reads IDMT keys, fetches linked Jira Themes, groups those Themes by
Business Value Stream, and reports tickets whose linked Themes are clean,
duplicated, missing, or failed during Jira access.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from scripts.build_stage_ground_truth import JiraApiClient
from vs_app.modules.stages.stage_ground_truth import (
    BUSINESS_VALUE_STREAM_FIELD,
    IDMT_FIELDS,
    THEME_FIELDS,
    find_linked_theme_issues,
    normalize_ticket_key,
    parse_business_value_stream,
)


DEFAULT_INPUT = Path("data/ticket_ids.txt")
DEFAULT_OUTPUT_DIR = Path("output/theme_duplicate_scan")

CLEAN_TICKET_IDS = "clean_ticket_ids.txt"
DUPLICATE_TICKET_IDS = "duplicate_ticket_ids.txt"
NO_THEME_TICKET_IDS = "no_theme_ticket_ids.txt"
ERROR_TICKET_IDS = "error_ticket_ids.txt"
REPORT_JSON = "idmt_theme_duplicate_report.json"
REPORT_CSV = "idmt_theme_duplicate_report.csv"

REQUIRED_IDMT_FIELDS = IDMT_FIELDS
REQUIRED_THEME_FIELDS = THEME_FIELDS

INPUT_ENV = "IDMT_THEME_SCAN_INPUT"
OUTPUT_DIR_ENV = "IDMT_THEME_SCAN_OUTPUT_DIR"
JIRA_BASE_URL_ENV = "JIRA_BASE_URL"
JIRA_TOKEN_ENV = "JIRA_TOKEN"

STATUS_CLEAN = "clean"
STATUS_DUPLICATE = "duplicate"
STATUS_NO_THEME = "no_theme"
STATUS_ERROR = "error"
MISSING_VALUE_STREAM = "(missing value stream)"

CSV_COLUMNS = [
    "idmt_key",
    "idmt_summary",
    "status_bucket",
    "theme_count",
    "value_stream_count",
    "duplicate_value_streams",
    "duplicate_theme_keys",
    "themes_summary",
    "error",
]


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    input_path = Path(os.getenv(INPUT_ENV, str(DEFAULT_INPUT)))
    output_dir = Path(os.getenv(OUTPUT_DIR_ENV, str(DEFAULT_OUTPUT_DIR)))
    ticket_ids = read_ticket_ids(input_path)

    if not ticket_ids:
        raise SystemExit(f"No IDMT ticket IDs found in {input_path}")

    jira_base_url = os.getenv(JIRA_BASE_URL_ENV, "").strip()
    jira_token = os.getenv(JIRA_TOKEN_ENV, "").strip()
    if not jira_base_url or not jira_token:
        raise SystemExit("Set JIRA_BASE_URL and JIRA_TOKEN before running the scan.")

    async with JiraApiClient(
        base_url=jira_base_url,
        token=jira_token,
        verify_ssl=False,
    ) as jira_client:
        report = await scan_ticket_ids(
            ticket_ids,
            jira_client=jira_client,
            input_path=input_path,
        )

    write_report_outputs(report, output_dir)
    print_summary(report, output_dir)
    return 0


async def scan_ticket_ids(
    ticket_ids: list[str],
    *,
    jira_client: JiraApiClient,
    input_path: Path,
) -> dict[str, Any]:
    normalized_ticket_ids = dedupe_ticket_ids(ticket_ids)
    ticket_rows: list[dict[str, Any]] = []
    for ticket_id in normalized_ticket_ids:
        ticket_rows.append(await scan_single_ticket(ticket_id, jira_client=jira_client))

    return {
        "source": "jira",
        "generated_at": utc_now(),
        "input": str(input_path),
        "ticket_count": len(normalized_ticket_ids),
        "summary": build_duplicate_summary(ticket_rows),
        "tickets": ticket_rows,
    }


async def scan_single_ticket(
    ticket_id: str,
    *,
    jira_client: JiraApiClient,
) -> dict[str, Any]:
    idmt_key = normalize_ticket_key(ticket_id)
    idmt_issue: dict[str, Any] = {}
    try:
        idmt_issue = await fetch_idmt_issue(jira_client, idmt_key)
        theme_refs = find_linked_theme_issues(idmt_issue)
        theme_issues = [
            await fetch_theme_issue(jira_client, str(theme_ref.get("key") or ""))
            for theme_ref in theme_refs
            if str(theme_ref.get("key") or "").strip()
        ]
        theme_rows = extract_theme_rows(theme_refs=theme_refs, theme_issues=theme_issues)
        grouped = group_by_value_stream(theme_rows)
        return classify_ticket(
            idmt_key=idmt_key,
            idmt_issue=idmt_issue,
            theme_rows=theme_rows,
            grouped_themes=grouped,
        )
    except Exception as exc:
        return classify_ticket(
            idmt_key=idmt_key,
            idmt_issue={},
            theme_rows=[],
            grouped_themes={},
            error=compact_error(exc),
        )


async def fetch_idmt_issue(jira_client: JiraApiClient, ticket_key: str) -> dict[str, Any]:
    return await jira_client.get_issue(ticket_key, fields=REQUIRED_IDMT_FIELDS, expand=True)


async def fetch_theme_issue(jira_client: JiraApiClient, theme_key: str) -> dict[str, Any]:
    return await jira_client.get_issue(theme_key, fields=REQUIRED_THEME_FIELDS, expand=True)


def extract_theme_rows(
    *,
    theme_refs: list[dict[str, Any]],
    theme_issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ref_by_key = {
        normalize_ticket_key(theme_ref.get("key")): theme_ref
        for theme_ref in theme_refs
        if normalize_ticket_key(theme_ref.get("key"))
    }
    return [
        build_theme_row(
            theme_issue,
            theme_ref=ref_by_key.get(normalize_ticket_key(theme_issue.get("key"))),
        )
        for theme_issue in theme_issues
        if normalize_ticket_key(theme_issue.get("key"))
    ]


def group_by_value_stream(theme_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in theme_rows:
        value_stream_name = row.get("value_stream_name") or MISSING_VALUE_STREAM
        grouped.setdefault(str(value_stream_name), []).append(row)
    return grouped


def classify_ticket(
    *,
    idmt_key: str,
    idmt_issue: dict[str, Any],
    theme_rows: list[dict[str, Any]],
    grouped_themes: dict[str, list[dict[str, Any]]],
    error: str = "",
) -> dict[str, Any]:
    duplicate_groups = {
        value_stream: rows
        for value_stream, rows in grouped_themes.items()
        if len(rows) > 1
    }
    status_bucket = ticket_status_bucket(
        theme_rows=theme_rows,
        duplicate_groups=duplicate_groups,
        error=error,
    )

    return {
        "idmt_key": idmt_key,
        "idmt_summary": issue_summary(idmt_issue),
        "status_bucket": status_bucket,
        "theme_count": len(theme_rows),
        "value_stream_count": len(grouped_themes),
        "duplicate_value_streams": sorted(duplicate_groups),
        "duplicate_theme_keys": duplicate_theme_keys(duplicate_groups),
        "themes_summary": themes_summary(theme_rows),
        "themes": theme_rows,
        "themes_by_value_stream": grouped_theme_keys(grouped_themes),
        "error": error,
    }


def build_duplicate_summary(ticket_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {bucket: 0 for bucket in (STATUS_CLEAN, STATUS_DUPLICATE, STATUS_NO_THEME, STATUS_ERROR)}
    for row in ticket_rows:
        bucket = str(row.get("status_bucket") or STATUS_ERROR)
        counts[bucket] = counts.get(bucket, 0) + 1

    return {
        "ticket_count": len(ticket_rows),
        "clean_count": counts.get(STATUS_CLEAN, 0),
        "duplicate_count": counts.get(STATUS_DUPLICATE, 0),
        "no_theme_count": counts.get(STATUS_NO_THEME, 0),
        "error_count": counts.get(STATUS_ERROR, 0),
        "status_counts": counts,
    }


def write_report_outputs(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    ticket_rows = list(report.get("tickets") or [])

    write_text_list(
        output_dir / CLEAN_TICKET_IDS,
        ticket_ids_for_bucket(ticket_rows, STATUS_CLEAN),
    )
    write_text_list(
        output_dir / DUPLICATE_TICKET_IDS,
        ticket_ids_for_bucket(ticket_rows, STATUS_DUPLICATE),
    )
    write_text_list(
        output_dir / NO_THEME_TICKET_IDS,
        ticket_ids_for_bucket(ticket_rows, STATUS_NO_THEME),
    )
    write_text_list(
        output_dir / ERROR_TICKET_IDS,
        ticket_ids_for_bucket(ticket_rows, STATUS_ERROR),
    )

    (output_dir / REPORT_JSON).write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_csv_report(output_dir / REPORT_CSV, ticket_rows)


def write_text_list(path: Path, ticket_ids: list[str]) -> None:
    path.write_text(
        "".join(f"{ticket_id}\n" for ticket_id in ticket_ids),
        encoding="utf-8",
    )


def write_csv_report(path: Path, ticket_rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in ticket_rows:
            writer.writerow(csv_row(row))


def print_summary(report: dict[str, Any], output_dir: Path) -> None:
    summary = report.get("summary") or {}
    print("IDMT Theme duplicate scan complete")
    print(f"Tickets scanned: {summary.get('ticket_count', 0)}")
    print(f"Clean: {summary.get('clean_count', 0)}")
    print(f"Duplicate: {summary.get('duplicate_count', 0)}")
    print(f"No Theme: {summary.get('no_theme_count', 0)}")
    print(f"Error: {summary.get('error_count', 0)}")
    print(f"Output directory: {output_dir}")
    print(f"JSON report: {output_dir / REPORT_JSON}")
    print(f"CSV report: {output_dir / REPORT_CSV}")


def read_ticket_ids(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Input file not found: {path}")

    ticket_ids: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.split("#", 1)[0].strip()
        ticket_ids.extend(split_ticket_text(text))
    return dedupe_ticket_ids(ticket_ids)


def build_theme_row(
    theme_issue: dict[str, Any],
    *,
    theme_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = theme_issue.get("fields") or {}
    business_value_stream = parse_business_value_stream(fields.get(BUSINESS_VALUE_STREAM_FIELD))
    theme_key = normalize_ticket_key(theme_issue.get("key"))

    return {
        "theme_key": theme_key,
        "theme_summary": clean_text(fields.get("summary") or (theme_ref or {}).get("summary")),
        "theme_issue_type": issue_type_name(theme_issue)
        or clean_text((theme_ref or {}).get("issue_type")),
        "business_value_stream": business_value_stream,
        "value_stream_name": business_value_stream.get("name") or "",
        "value_stream_id": business_value_stream.get("id") or "",
    }


def ticket_status_bucket(
    *,
    theme_rows: list[dict[str, Any]],
    duplicate_groups: dict[str, list[dict[str, Any]]],
    error: str,
) -> str:
    if error:
        return STATUS_ERROR
    if not theme_rows:
        return STATUS_NO_THEME
    if duplicate_groups:
        return STATUS_DUPLICATE
    return STATUS_CLEAN


def duplicate_theme_keys(duplicate_groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    keys: list[str] = []
    for rows in duplicate_groups.values():
        keys.extend(str(row.get("theme_key") or "") for row in rows if row.get("theme_key"))
    return sorted(set(keys))


def grouped_theme_keys(grouped_themes: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {
        value_stream: [
            str(row.get("theme_key") or "")
            for row in rows
            if str(row.get("theme_key") or "").strip()
        ]
        for value_stream, rows in sorted(grouped_themes.items())
    }


def themes_summary(theme_rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in theme_rows:
        theme_key = row.get("theme_key") or ""
        value_stream = row.get("value_stream_name") or MISSING_VALUE_STREAM
        parts.append(f"{theme_key}: {value_stream}")
    return "; ".join(parts)


def ticket_ids_for_bucket(ticket_rows: list[dict[str, Any]], bucket: str) -> list[str]:
    return [
        str(row.get("idmt_key") or "")
        for row in ticket_rows
        if row.get("status_bucket") == bucket and row.get("idmt_key")
    ]


def csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "idmt_key": row.get("idmt_key") or "",
        "idmt_summary": row.get("idmt_summary") or "",
        "status_bucket": row.get("status_bucket") or "",
        "theme_count": row.get("theme_count") or 0,
        "value_stream_count": row.get("value_stream_count") or 0,
        "duplicate_value_streams": join_values(row.get("duplicate_value_streams") or []),
        "duplicate_theme_keys": join_values(row.get("duplicate_theme_keys") or []),
        "themes_summary": row.get("themes_summary") or "",
        "error": row.get("error") or "",
    }


def issue_summary(issue: dict[str, Any]) -> str:
    return clean_text((issue.get("fields") or {}).get("summary"))


def issue_type_name(issue: dict[str, Any]) -> str:
    issue_type = (issue.get("fields") or {}).get("issuetype") or {}
    if isinstance(issue_type, dict):
        return clean_text(issue_type.get("name"))
    return clean_text(issue_type)


def split_ticket_text(value: str) -> list[str]:
    return [part for part in re.split(r"[\s,]+", str(value or "").strip()) if part]


def dedupe_ticket_ids(ticket_ids: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ticket_id in ticket_ids:
        normalized = normalize_ticket_key(ticket_id)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def join_values(values: list[Any]) -> str:
    return " | ".join(str(value) for value in values if str(value).strip())


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
