#!/usr/bin/env python3
"""
Find Jira customfield_XXXXX IDs and values for IDMT / linked Theme tickets.

Uses repo-style env vars:
  JIRA_BASE_URL=https://jira.fyiblue.com
  JIRA_TOKEN=...

No email is required.

Usage:
  py -3 scripts/find_jira_custom_fields.py --issue GROUP-22221 --issue IDMT-19761

Output:
  output/jira_custom_field_mapping.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests


TARGET_FIELD_NAMES = {
    "Project I-Code",
    "Business Value Stream",
    "Business Need Summary",
    "L2 Business Capability Model",
    "L3 Business Capability Model",
    "Assumptions",
}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var: {name}")
    return value


def auth_headers(token: str) -> dict[str, str]:
    """
    Supports:
    - raw PAT/token: JIRA_TOKEN=abc123
    - already-prefixed token: JIRA_TOKEN="Bearer abc123"
    - already-prefixed basic token: JIRA_TOKEN="Basic abc123"
    """
    token = token.strip()

    if token.lower().startswith(("bearer ", "basic ")):
        authorization = token
    else:
        authorization = f"Bearer {token}"

    return {
        "Accept": "application/json",
        "Authorization": authorization,
    }


def jira_get(base_url: str, path: str, headers: dict[str, str]) -> Any:
    url = f"{base_url.rstrip('/')}{path}"

    response = requests.get(
        url,
        headers=headers,
        timeout=60,
        verify=False,  # matches repo Settings default verify_ssl=False
    )

    if response.status_code >= 400:
        print(f"\nERROR {response.status_code} calling:\n{url}\n", file=sys.stderr)
        print(response.text[:3000], file=sys.stderr)
        raise SystemExit(1)

    return response.json()


def normalize(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def compact_value(value: Any) -> Any:
    """
    Make Jira custom field values readable.
    Handles dict/list/select-list style fields.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ("value", "name", "key", "id"):
            if value.get(key):
                return value[key]
        return value

    if isinstance(value, list):
        return [compact_value(item) for item in value]

    return value


def get_field_registry(base_url: str, headers: dict[str, str]) -> dict[str, str]:
    fields = jira_get(base_url, "/rest/api/2/field", headers)

    mapping: dict[str, str] = {}
    for field in fields:
        field_id = str(field.get("id") or "").strip()
        field_name = str(field.get("name") or "").strip()
        if field_id and field_name:
            mapping[field_name] = field_id

    return mapping


def get_issue(base_url: str, issue_key: str, headers: dict[str, str]) -> dict[str, Any]:
    return jira_get(
        base_url,
        f"/rest/api/2/issue/{issue_key}?expand=names,schema",
        headers,
    )


def extract_linked_issues(issue_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for link in issue_links or []:
        link_type = (link.get("type") or {}).get("name", "")

        if "outwardIssue" in link:
            direction = "outward"
            linked = link["outwardIssue"]
        elif "inwardIssue" in link:
            direction = "inward"
            linked = link["inwardIssue"]
        else:
            continue

        linked_fields = linked.get("fields") or {}

        output.append(
            {
                "link_type": link_type,
                "direction": direction,
                "key": linked.get("key"),
                "issue_type": (linked_fields.get("issuetype") or {}).get("name"),
                "summary": linked_fields.get("summary"),
            }
        )

    return output


def inspect_issue(issue: dict[str, Any]) -> dict[str, Any]:
    names = issue.get("names") or {}
    fields = issue.get("fields") or {}

    target_by_norm = {normalize(name): name for name in TARGET_FIELD_NAMES}
    matched_custom_fields: dict[str, Any] = {}

    for field_id, display_name in names.items():
        canonical_name = target_by_norm.get(normalize(display_name))
        if not canonical_name:
            continue

        matched_custom_fields[canonical_name] = {
            "field_id": field_id,
            "api_path": f"fields.{field_id}",
            "value": compact_value(fields.get(field_id)),
        }

    return {
        "key": issue.get("key"),
        "id": issue.get("id"),
        "issue_type": (fields.get("issuetype") or {}).get("name"),
        "summary": fields.get("summary"),
        "project": {
            "key": (fields.get("project") or {}).get("key"),
            "name": (fields.get("project") or {}).get("name"),
        },
        "matched_custom_fields": matched_custom_fields,
        "linked_issues": extract_linked_issues(fields.get("issuelinks") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issue",
        action="append",
        default=[],
        help="Jira issue key, e.g. GROUP-22221 or IDMT-19761. Can be repeated.",
    )
    parser.add_argument(
        "--json-out",
        default="output/jira_custom_field_mapping.json",
        help="Output JSON path.",
    )
    args = parser.parse_args()

    if not args.issue:
        raise SystemExit("Provide at least one --issue, example: --issue GROUP-22221")

    base_url = required_env("JIRA_BASE_URL")
    token = required_env("JIRA_TOKEN")
    headers = auth_headers(token)

    print("Fetching Jira field registry...")
    registry = get_field_registry(base_url, headers)

    registry_matches: dict[str, str] = {}
    target_by_norm = {normalize(name): name for name in TARGET_FIELD_NAMES}

    for field_name, field_id in registry.items():
        canonical_name = target_by_norm.get(normalize(field_name))
        if canonical_name:
            registry_matches[canonical_name] = field_id

    issues: dict[str, Any] = {}

    for issue_key in args.issue:
        issue_key = issue_key.strip()
        print(f"Inspecting {issue_key}...")
        issue = get_issue(base_url, issue_key, headers)
        issues[issue_key] = inspect_issue(issue)

    result = {
        "target_fields": sorted(TARGET_FIELD_NAMES),
        "field_registry_matches": registry_matches,
        "issues": issues,
    }

    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\nCUSTOM FIELD IDS")
    print("================")
    for name in sorted(TARGET_FIELD_NAMES):
        print(f"{name}: {registry_matches.get(name, 'NOT FOUND')}")

    print("\nISSUE FIELD VALUES")
    print("==================")
    for issue_key, info in issues.items():
        print(f"\n{issue_key}")
        print(f"  Issue Type: {info.get('issue_type')}")
        print(f"  Summary: {info.get('summary')}")

        for field_name, field_info in info.get("matched_custom_fields", {}).items():
            print(f"  {field_name}:")
            print(f"    field_id: {field_info['field_id']}")
            print(f"    api_path: {field_info['api_path']}")
            print(f"    value: {field_info['value']}")

        linked = info.get("linked_issues") or []
        if linked:
            print("  Linked Issues:")
            for item in linked:
                print(
                    f"    - {item['link_type']} | {item['direction']} | "
                    f"{item['key']} | {item['issue_type']} | {item['summary']}"
                )

    print(f"\nWrote JSON: {output_path}")


if __name__ == "__main__":
    main()