"""Run a simple Parent Link-only stage ground-truth smoke test."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
from pprint import pprint
from typing import Any

from scripts.build_stage_ground_truth import JiraApiClient, write_outputs
from vs_app.modules.stages.stage_catalog import load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import build_ticket_stage_ground_truth


DEFAULT_TICKET_KEY = "IDMT-19761"
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_OUTPUT = Path("output/stage_eval/stage_ground_truth_smoke.json")
DEFAULT_FOCUS_THEME = "GROUP-22223"


def main() -> int:
    return asyncio.run(async_main())


async def async_main() -> int:
    ticket_key = os.getenv("STAGE_GT_TICKET_KEY", DEFAULT_TICKET_KEY).strip()
    focus_theme = os.getenv("STAGE_GT_FOCUS_THEME", DEFAULT_FOCUS_THEME).strip()
    output_path = Path(os.getenv("STAGE_GT_OUTPUT", str(DEFAULT_OUTPUT)))
    jira_base_url = os.getenv("JIRA_BASE_URL", "").strip()
    jira_token = os.getenv("JIRA_TOKEN", "").strip()
    if not jira_base_url or not jira_token:
        raise SystemExit("Set JIRA_BASE_URL and JIRA_TOKEN before running stage_gt_smoke.")

    catalog = load_stage_catalog(path=DEFAULT_STAGE_CATALOG, source="json")
    async with JiraApiClient(base_url=jira_base_url, token=jira_token, verify_ssl=False) as jira:
        ticket_gt = await build_ticket_stage_ground_truth(
            ticket_key=ticket_key,
            jira_client=jira,
            catalog=catalog,
        )

    payload = {
        "source": "jira",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stage_catalog_source": "json",
        "stage_catalog": str(DEFAULT_STAGE_CATALOG),
        "stage_gt_lookup_strategy": "parent_link_only",
        "tickets": {ticket_gt["idmt_key"]: ticket_gt},
    }
    write_outputs(payload, output_path)
    print(f"Wrote smoke GT: {output_path}")
    _print_summary(ticket_gt, focus_theme=focus_theme)
    return 0


def _print_summary(ticket_gt: dict[str, Any], *, focus_theme: str) -> None:
    print("\nSTAGE GT SMOKE SUMMARY")
    print(f"IDMT: {ticket_gt.get('idmt_key')}")
    linked_themes = list(ticket_gt.get("linked_themes") or [])
    print(f"Linked Theme count: {len(linked_themes)}")

    for theme in linked_themes:
        value_stream = (theme.get("business_value_stream") or {}).get("name") or ""
        child_keys = list((theme.get("child_issue_lookup") or {}).get("child_issue_keys") or [])
        verified = [stage.get("canonical") for stage in theme.get("verified_stages") or []]
        print("\n" + "-" * 80)
        print(f"Theme: {theme.get('theme_key')}")
        print(f"Value stream: {value_stream}")
        print(f"Parent Link children count: {len(child_keys)}")
        print(f"Parent Link children: {', '.join(child_keys) if child_keys else '(none)'}")
        print(f"Verified stages: {', '.join(verified) if verified else '(none)'}")
        warnings = list(theme.get("warnings") or [])
        if warnings:
            print("Warnings:")
            for warning in warnings:
                print(f"  - {warning}")

    focused = next((theme for theme in linked_themes if theme.get("theme_key") == focus_theme), None)
    if not focused:
        print(f"\nFocused Theme {focus_theme} not found.")
        return

    print("\n" + "=" * 80)
    print(f"Focused Theme: {focus_theme}")
    print("Child lookup:")
    pprint(focused.get("child_issue_lookup") or {})
    print("Child issues:")
    pprint(focused.get("child_issues") or [])
    print("Child stage mentions:")
    pprint(focused.get("child_issue_mentions_debug") or [])
    print("Canonicalization:")
    pprint(focused.get("canonicalization_debug") or [])
    print("Verified stages:")
    pprint(focused.get("verified_stages") or [])

    value_stream = (focused.get("business_value_stream") or {}).get("name") or ""
    final_gt = list((ticket_gt.get("gt_by_value_stream") or {}).get(value_stream) or [])
    for mention in focused.get("child_issue_mentions_debug") or []:
        print(f"Cleaned stage: {mention.get('raw_stage')}")
    for item in focused.get("canonicalization_debug") or []:
        print(f"Canonical stage: {item.get('canonical')}")
    print(f"Final GT: {value_stream} -> {', '.join(final_gt) if final_gt else '(none)'}")


if __name__ == "__main__":
    raise SystemExit(main())
