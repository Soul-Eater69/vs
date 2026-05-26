#!/usr/bin/env python3
"""
Find Jira customfield_XXXXX IDs for fields used by IDMT / linked Theme tickets.

Usage:

PowerShell:
  $env:JIRA_BASE_URL="https://jira.fyiblue.com"
  $env:JIRA_EMAIL="your_email"
  $env:JIRA_API_TOKEN="your_token_or_password"
  py -3 scripts/find_jira_custom_fields.py --issue GROUP-22221

Optional:
  py -3 scripts/find_jira_custom_fields.py --issue IDMT-19761 --issue GROUP-22221
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var: {name}")
    return value


def jira_get(url: str, auth: tuple[str, str]) -> dict[str, Any] | list[dict[str, Any]]:
    response = requests.get(
        url,
        auth=auth,
        headers={"Accept": "application/json"},
        timeout=60,
    )
    if response.status_code >= 400:
        print(f"\nERROR {response.status_code} calling:\n{url}\n", file=sys.stderr)
        print(response.text[:2000], file=sys.stderr)
        raise SystemExit(1)
    return response.json()


def normalize(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def get_all_fields(base_url: str, auth: tuple[str, str]) -> dict[str, str]:
    url = f"{base_url.rstrip('/')}/rest/api/2/field"
    fields = jira_get(url, auth)

    mapping: dict[str, str] = {}
    for field in fields:
        field_id = str(field.get("id", "")).strip()
        field_name = str(field.get("name", "")).strip()
        if field_id and field_name:
            mapping[field_name] = field_id

    return mapping


def get_issue_expanded(base_url: str, issue_key: str, auth: tuple[str, str]) -> dict[str, Any]:
    url = (
        f"{base_url.rstrip()}/rest/api/2/issue/{issue_key}"
        f"?expand=names,schema"
    )
    return jira_get(url, auth)


def compact_value(value: Any) -> Any:
    """Make Jira custom field values easier to read."""
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ("value", "name", "key", "id"):
            if key in value and value[key]:
                return value[key]
        return value

    if isinstance(value, list):
        output = []
        for item in value:
            output.append(compact_value(item))
        return output

    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issue",
        action="append",
        default=[],
        help="Jira issue key to inspect, e.g. GROUP-22221 or IDMT-19761. Can be repeated.",
    )
    parser.add_argument(
        "--json-out",
        default="output/jira_custom_field_mapping.json",
        help="Where to write the mapping JSON.",
    )
    args = parser.parse_args()

    if not args.issue:
        raise SystemExit("Provide at least one --issue, e.g. --issue GROUP-22221")

    base_url = env_required("JIRA_BASE_URL")
    email = env_required("JIRA_EMAIL")
    token = env_required("JIRA_API_TOKEN")
    auth = (email, token)

    print("Fetching Jira field registry...")
    field_registry = get_all_fields(base_url, auth)

    target_norms = {normalize(name): name for name in TARGET_FIELD_NAMES}

    registry_matches: dict[str, str] = {}
    for field_name, field_id in field_registry.items():
        norm = normalize(field_name)
        if norm in target_norms:
            registry_matches[target_norms[norm]] = field_id

    issue_details: dict[str, Any] = {}

    for issue_key in args.issue:
        issue_key = issue_key.strip()
        print(f"Inspecting issue {issue_key}...")
        issue = get_issue_expanded(base_url, issue_key, auth)

        names = issue.get("names", {}) or {}
        fields = issue.get("fields", {}) or {}

        matched_fields: dict[str, Any] = {}

        for field_id, display_name in names.items():
            display_name = str(display_name).strip()
            if normalize(display_name) in target_norms:
                canonical_name = target_norms[normalize(display_name)]
                matched_fields[canonical_name] = {
                    "field_id": field_id,
                    "api_path": f"fields.{field_id}",
                    "value": compact_value(fields.get(field_id)),
                }

        issue_details[issue_key] = {
            "key": issue.get("key"),
            "id": issue.get("id"),
            "issue_type": fields.get("issuetype", {}).get("name"),
            "summary": fields.get("summary"),
            "matched_custom_fields": matched_fields,
            "linked_issues": extract_linked_issues(fields.get("issuelinks") or []),
        }

    final = {
        "target_fields": sorted(TARGET_FIELD_NAMES),
        "field_registry_matches": registry_matches,
        "issues": issue_details,
    }

    os.makedirs(os.path.dirname(args.json_out), exist_ok=True)
    with open(args.json_out, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    print("\nCUSTOM FIELD MAPPING")
    print("====================")
    for name in sorted(TARGET_FIELD_NAMES):
        field_id = registry_matches.get(name)
        print(f"{name}: {field_id or 'NOT FOUND IN /field'}")

    print("\nISSUE VALUES")
    print("============")
    for issue_key, details in issue_details.items():
        print(f"\n{issue_key}")
        print(f"  Issue Type: {details.get('issue_type')}")
        print(f"  Summary: {details.get('summary')}")
        for field_name, info in details["matched_custom_fields"].items():
            print(f"  {field_name}:")
            print(f"    field_id: {info['field_id']}")
            print(f"    api_path: {info['api_path']}")
            print(f"    value: {info['value']}")

        if details["linked_issues"]:
            print("  Linked Issues:")
            for linked in details["linked_issues"]:
                print(
                    f"    - {linked['link_type']} | {linked['direction']} | "
                    f"{linked['key']} | {linked['issue_type']} | {linked['summary']}"
                )

    print(f"\nWrote JSON: {args.json_out}")


def extract_linked_issues(issue_links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []

    for link in issue_links:
        link_type = (link.get("type") or {}).get("name", "")

        if "outwardIssue" in link:
            linked = link["outwardIssue"]
            direction = "outward"
        elif "inwardIssue" in link:
            linked = link["inwardIssue"]
            direction = "inward"
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


if __name__ == "__main__":
    main()