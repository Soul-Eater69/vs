"""Inspect stage ground-truth traces for one or more tickets/themes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from pprint import pprint
from typing import Any


def main() -> int:
    args = parse_args()
    payload = json.loads(Path(args.stage_ground_truth).read_text(encoding="utf-8"))
    tickets = payload.get("tickets") or {}

    ticket_ids = [args.ticket_id] if args.ticket_id else sorted(tickets)
    if not ticket_ids:
        raise SystemExit("No tickets found in stage ground-truth file.")

    for ticket_id in ticket_ids:
        ticket = tickets.get(ticket_id)
        if not ticket:
            print(f"\nTicket {ticket_id} not found.")
            continue
        print_ticket(ticket_id, ticket, theme_key=args.theme_key)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect stage ground-truth debug traces.")
    parser.add_argument(
        "--stage-ground-truth",
        required=True,
        help="Path to stage_ground_truth.json.",
    )
    parser.add_argument("--ticket-id", help="Optional ticket key to inspect.")
    parser.add_argument("--theme-key", help="Optional linked Theme key to inspect.")
    return parser.parse_args()


def print_ticket(ticket_id: str, ticket: dict[str, Any], *, theme_key: str | None) -> None:
    print("\n" + "=" * 100)
    print(f"ticket id: {ticket_id}")
    print("gt_by_value_stream:")
    pprint(ticket.get("gt_by_value_stream") or {})

    themes = list(ticket.get("linked_themes") or [])
    if theme_key:
        themes = [theme for theme in themes if theme.get("theme_key") == theme_key]
        if not themes:
            print(f"theme key {theme_key} not found for {ticket_id}.")
            return

    for theme in themes:
        print_theme(ticket, theme)


def print_theme(ticket: dict[str, Any], theme: dict[str, Any]) -> None:
    business_value_stream = theme.get("business_value_stream") or {}
    value_stream_name = business_value_stream.get("name") or ""
    final_gt = list((ticket.get("gt_by_value_stream") or {}).get(value_stream_name) or [])

    print("\n" + "-" * 100)
    print(f"theme key: {theme.get('theme_key')}")
    print(f"theme summary: {theme.get('theme_summary')}")
    print("business value stream:")
    pprint(business_value_stream)
    print("allowed stages:")
    pprint(theme.get("allowed_stages") or [])
    print("child_issue_lookup:")
    pprint(theme.get("child_issue_lookup") or {})
    print("child_issues:")
    pprint(theme.get("child_issues") or [])
    print("child_issue_mentions_debug:")
    pprint(theme.get("child_issue_mentions_debug") or [])
    print("canonicalization_debug:")
    pprint(theme.get("canonicalization_debug") or [])
    print("verified_stages:")
    pprint(theme.get("verified_stages") or [])
    print("unresolved_stage_mentions:")
    pprint(theme.get("unresolved_stage_mentions") or [])

    child_lookup = theme.get("child_issue_lookup") or {}
    child_issues = list(theme.get("child_issues") or [])
    child_issue_keys = list(child_lookup.get("child_issue_keys") or [])
    child_mentions = list(theme.get("child_issue_mentions_debug") or [])
    verified_stages = list(theme.get("verified_stages") or [])

    print("\nDIAGNOSIS:")
    print(f"- child issues fetched? {_yes_no(bool(child_issues or child_issue_keys))}")
    print(f"- child stage mentions extracted? {_yes_no(bool(child_mentions))}")
    print(f"- canonical stages verified? {_yes_no(bool(verified_stages))}")
    print(f"- final gt_by_value_stream contains stages? {_yes_no(bool(final_gt))}")


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


if __name__ == "__main__":
    raise SystemExit(main())
