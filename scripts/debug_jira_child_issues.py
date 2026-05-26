"""Debug how Jira exposes Theme child issues / Epic children.

This is an offline diagnostic helper: it reads Jira issues and JQL results, then
writes local JSON artifacts for inspection. It does not modify Jira.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
SCRIPTS_ROOT = REPO_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from build_stage_ground_truth import JiraApiClient


DEFAULT_PARENT = "GROUP-22223"
DEFAULT_CHILDREN = ["GROUP-22805", "GROUP-22838"]
DEFAULT_OUTPUT_DIR = Path("output/jira_child_debug")
INTERESTING_TERMS = [
    "parent",
    "epic",
    "theme",
    "portfolio",
    "hierarchy",
    "structure",
    "initiative",
    "child",
]


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    args = parse_args()
    parent_key = normalize_key(args.parent)
    child_keys = [normalize_key(child) for child in args.child if normalize_key(child)]
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not args.jira_base_url or not args.jira_token:
        raise SystemExit("Jira access requires JIRA_BASE_URL and JIRA_TOKEN.")

    async with JiraApiClient(
        base_url=args.jira_base_url,
        token=args.jira_token,
        verify_ssl=bool(args.verify_ssl),
    ) as jira:
        parent_issue = await jira.get_issue(parent_key, fields=None, expand=True)
        write_json(output_dir / f"{parent_key}_all_fields.json", parent_issue)
        parent_interesting = find_interesting_fields(
            parent_issue,
            INTERESTING_TERMS,
            parent_key=parent_key,
        )
        parent_metadata_matches = find_name_schema_matches(parent_issue, INTERESTING_TERMS)

        print_issue_summary("PARENT", parent_issue)
        print(f"  subtasks exists: {subtasks_exist(parent_issue)}")
        print(f"  subtasks count: {subtask_count(parent_issue)}")
        print(f"  names/schema hierarchy matches: {len(parent_metadata_matches)}")
        print_field_rows(parent_metadata_matches, indent="    ")

        child_issues: dict[str, dict[str, Any]] = {}
        child_interesting: dict[str, list[dict[str, Any]]] = {}
        child_parent_field_rows: dict[str, list[dict[str, Any]]] = {}
        for child_key in child_keys:
            issue = await jira.get_issue(child_key, fields=None, expand=True)
            child_issues[child_key] = issue
            write_json(output_dir / f"{child_key}_all_fields.json", issue)
            rows = find_interesting_fields(
                issue,
                INTERESTING_TERMS,
                parent_key=parent_key,
            )
            child_interesting[child_key] = rows
            child_parent_field_rows[child_key] = [
                row for row in rows if "value_contains_parent" in row.get("matched_by", [])
            ]

            print_issue_summary("CHILD", issue)
            print("  interesting hierarchy fields:")
            print_field_rows(rows, indent="    ")

        parent_summary = issue_summary(parent_issue)
        jql_attempts = []
        for jql in build_jqls(parent_key, parent_summary=parent_summary):
            attempt = await run_jql_attempt(jira, jql)
            jql_attempts.append(attempt)
            if attempt["ok"]:
                keys = [issue["key"] for issue in attempt["issues"]]
                print(f"JQL OK count={attempt['count']}: {jql}")
                print(f"  keys: {keys}")
            else:
                print(f"JQL ERROR: {jql}")
                print(f"  {attempt['error']}")

    result = {
        "parent": parent_key,
        "children": child_keys,
        "jql_attempts": jql_attempts,
        "parent_interesting_fields": parent_interesting,
        "parent_metadata_matches": parent_metadata_matches,
        "child_interesting_fields": child_interesting,
    }
    write_json(output_dir / "jql_results.json", result)

    print_final_summary(
        parent_key=parent_key,
        child_keys=child_keys,
        jql_attempts=jql_attempts,
        parent_interesting=parent_interesting,
        child_interesting=child_interesting,
        child_parent_field_rows=child_parent_field_rows,
    )
    print(f"\nWrote debug artifacts to {output_dir}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover how Jira exposes Theme child issues / Epic children.",
    )
    parser.add_argument("--parent", default=DEFAULT_PARENT, help="Parent Theme key.")
    parser.add_argument(
        "--child",
        action="append",
        default=[],
        help="Known child key. Can be repeated. Defaults to GROUP-22805 and GROUP-22838.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for full issue JSON and JQL result artifacts.",
    )
    parser.add_argument("--jira-base-url", default=os.getenv("JIRA_BASE_URL"))
    parser.add_argument("--jira-token", default=os.getenv("JIRA_TOKEN"))
    parser.add_argument("--verify-ssl", action="store_true", help="Verify Jira TLS certificates.")
    args = parser.parse_args()
    if not args.child:
        args.child = list(DEFAULT_CHILDREN)
    return args


def find_interesting_fields(
    issue: dict,
    terms: list[str],
    parent_key: str | None = None,
) -> list[dict[str, Any]]:
    names = issue.get("names") or {}
    fields = issue.get("fields") or {}
    lowered_terms = [term.lower() for term in terms]
    parent = str(parent_key or "").strip().upper()
    rows: list[dict[str, Any]] = []

    for field_id, value in fields.items():
        field_name = str(names.get(field_id) or field_id)
        name_text = f"{field_id} {field_name}".lower()
        matched_by: list[str] = []
        matched_terms = [term for term in lowered_terms if term in name_text]
        if matched_terms:
            matched_by.append("name")

        value_text = safe_json_dumps(value)
        if parent and parent in value_text.upper():
            matched_by.append("value_contains_parent")

        if matched_by:
            rows.append(
                {
                    "field_id": field_id,
                    "field_name": field_name,
                    "matched_by": matched_by,
                    "matched_terms": matched_terms,
                    "value_preview": preview(value),
                }
            )

    return sorted(rows, key=lambda row: (row["field_name"].lower(), row["field_id"]))


def find_name_schema_matches(issue: dict, terms: list[str]) -> list[dict[str, Any]]:
    names = issue.get("names") or {}
    schema = issue.get("schema") or {}
    lowered_terms = [term.lower() for term in terms]
    rows: list[dict[str, Any]] = []
    for field_id, field_name in names.items():
        haystack = f"{field_id} {field_name} {safe_json_dumps(schema.get(field_id))}".lower()
        matched_terms = [term for term in lowered_terms if term in haystack]
        if not matched_terms:
            continue
        rows.append(
            {
                "field_id": field_id,
                "field_name": str(field_name),
                "matched_terms": matched_terms,
                "schema_preview": preview(schema.get(field_id)),
            }
        )
    return sorted(rows, key=lambda row: (row["field_name"].lower(), row["field_id"]))


def build_jqls(parent_key: str, *, parent_summary: str) -> list[str]:
    summary = parent_summary or "CP 2026 Women's and Family Health : Manage Utilization Management Program"
    candidates = [
        f"parent = {parent_key}",
        f'issuekey in childIssuesOf("{parent_key}")',
        f'"Parent Link" = {parent_key}',
        f'"Epic Link" = {parent_key}',
        f'issueFunction in linkedIssuesOf("issuekey = {parent_key}")',
        f'issue in linkedIssues("{parent_key}")',
        'labels = WFH2026 AND issuetype = Epic AND summary ~ "Manage UM Operations"',
        'issuetype = Epic AND summary ~ "Manage Utilization Management Program"',
        f'issuetype = Epic AND summary ~ "{jql_quote(summary)}"',
    ]
    return dedupe(candidates)


async def run_jql_attempt(jira: JiraApiClient, jql: str) -> dict[str, Any]:
    try:
        result = await jira.search_issues(
            jql,
            fields=["summary", "issuetype", "status"],
            max_results=50,
        )
        issues = [compact_issue(issue) for issue in result.get("issues") or []]
        return {
            "jql": jql,
            "ok": True,
            "count": len(issues),
            "issues": issues,
            "error": None,
        }
    except Exception as exc:
        return {
            "jql": jql,
            "ok": False,
            "count": 0,
            "issues": [],
            "error": compact_error(exc),
        }


def print_final_summary(
    *,
    parent_key: str,
    child_keys: list[str],
    jql_attempts: list[dict[str, Any]],
    parent_interesting: list[dict[str, Any]],
    child_interesting: dict[str, list[dict[str, Any]]],
    child_parent_field_rows: dict[str, list[dict[str, Any]]],
) -> None:
    child_set = set(child_keys)
    matching_jqls: list[dict[str, Any]] = []
    for attempt in jql_attempts:
        returned = {issue["key"] for issue in attempt.get("issues") or []}
        known = sorted(returned & child_set)
        if known:
            matching_jqls.append(
                {
                    "jql": attempt["jql"],
                    "known_children": known,
                    "returned_all_known": child_set.issubset(returned),
                }
            )

    print("\n=== Compact Summary ===")
    print("JQLs that returned known child keys:")
    if matching_jqls:
        for row in matching_jqls:
            marker = "ALL" if row["returned_all_known"] else "PARTIAL"
            print(f"  [{marker}] {row['known_children']} <- {row['jql']}")
    else:
        print("  none")

    print("\nChild fields whose JSON value contains the parent key:")
    any_parent_field = False
    for child_key, rows in child_parent_field_rows.items():
        for row in rows:
            any_parent_field = True
            print(f"  {child_key}: {row['field_id']} / {row['field_name']} = {row['value_preview']}")
    if not any_parent_field:
        print("  none")

    print("\nCandidate hierarchy field names:")
    candidate_rows = hierarchy_candidate_rows(parent_interesting, child_interesting)
    if candidate_rows:
        for row in candidate_rows[:40]:
            print(f"  {row['field_id']} / {row['field_name']} ({', '.join(row['matched_terms'])})")
    else:
        print("  none")

    print("\nRecommended lookup strategy:")
    all_known = [row for row in matching_jqls if row["returned_all_known"]]
    if all_known:
        print(f"  Add this JQL pattern to _collect_child_issues: {all_known[0]['jql']}")
    elif any_parent_field:
        print(f"  Query by the field/customfield that contains {parent_key} on child issues.")
    else:
        print("  Use a summary-search fallback filtered to Epic and the parent Theme summary/stage suffix.")


def hierarchy_candidate_rows(
    parent_interesting: list[dict[str, Any]],
    child_interesting: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in parent_interesting:
        if "name" in row.get("matched_by", []):
            by_key.setdefault(row["field_id"], row)
    for rows in child_interesting.values():
        for row in rows:
            if "name" in row.get("matched_by", []):
                by_key.setdefault(row["field_id"], row)
    return sorted(by_key.values(), key=lambda row: (row["field_name"].lower(), row["field_id"]))


def print_issue_summary(label: str, issue: dict[str, Any]) -> None:
    fields = issue.get("fields") or {}
    print(f"\n{label} {issue.get('key')}")
    print(f"  summary: {issue_summary(issue)}")
    print(f"  issue type: {nested_name(fields.get('issuetype'))}")
    print(f"  field count: {len(fields)}")


def print_field_rows(rows: list[dict[str, Any]], *, indent: str = "") -> None:
    if not rows:
        print(f"{indent}none")
        return
    for row in rows[:40]:
        matched = row.get("matched_by") or row.get("matched_terms") or []
        print(f"{indent}{row['field_id']} / {row['field_name']} [{', '.join(matched)}]")
        value = row.get("value_preview") or row.get("schema_preview")
        if value:
            print(f"{indent}  {value}")
    if len(rows) > 40:
        print(f"{indent}... {len(rows) - 40} more")


def compact_issue(issue: dict[str, Any]) -> dict[str, str]:
    fields = issue.get("fields") or {}
    return {
        "key": str(issue.get("key") or ""),
        "summary": str(fields.get("summary") or ""),
        "issue_type": nested_name(fields.get("issuetype")),
        "status": nested_name(fields.get("status")),
    }


def issue_summary(issue: dict[str, Any]) -> str:
    return str((issue.get("fields") or {}).get("summary") or "")


def subtask_count(issue: dict[str, Any]) -> int:
    subtasks = (issue.get("fields") or {}).get("subtasks")
    return len(subtasks or []) if isinstance(subtasks, list) else 0


def subtasks_exist(issue: dict[str, Any]) -> bool:
    return "subtasks" in (issue.get("fields") or {})


def nested_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or value.get("key") or "")
    return str(value or "")


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def preview(value: Any, limit: int = 240) -> str:
    text = safe_json_dumps(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def compact_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        text = " ".join((exc.response.text or "").split())
        return f"HTTP {status}: {text[:500]}"
    return f"{type(exc).__name__}: {exc}"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def normalize_key(value: Any) -> str:
    return str(value or "").strip().upper()


def jql_quote(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
