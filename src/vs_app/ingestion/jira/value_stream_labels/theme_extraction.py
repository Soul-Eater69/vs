"""Extract themes (value streams) from Jira issue links."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .helpers import clean_value_stream_name, nested_str_field
from .theme_title_parser import parse_value_stream_from_theme_title

logger = logging.getLogger(__name__)


def extract_themes(
    issuelinks: List[Dict[str, Any]],
    *,
    source_title: str = "",
) -> List[Dict[str, Any]]:
    """Extract themes from implementation-style issue links.

    Returns a list of theme dicts with keys: key, summary, summary_raw, status.
    """
    themes: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for link in issuelinks:
        link_type = link.get("type", {}) or {}

        if _looks_like_implementation(link_type.get("outward")) and "outwardIssue" in link:
            _append_theme(themes, seen, link["outwardIssue"], source_title=source_title)

        if _looks_like_implementation(link_type.get("inward")) and "inwardIssue" in link:
            _append_theme(themes, seen, link["inwardIssue"], source_title=source_title)

        if _looks_like_implementation(link_type.get("name")):
            if "outwardIssue" in link:
                _append_theme(themes, seen, link["outwardIssue"], source_title=source_title)
            if "inwardIssue" in link:
                _append_theme(themes, seen, link["inwardIssue"], source_title=source_title)

    return themes


def resolve_value_streams(
    themes: List[Dict[str, Any]],
    issuelinks: List[Dict[str, Any]],
    llm_client: Any | None = None,
) -> Dict[str, Any]:
    """Resolve canonical value stream names from Jira GROUP/THEME links first.

    Returns dict with keys: value_stream_names, value_stream_ids,
    value_stream_statuses, value_stream_label_source.
    """
    try:
        from .link_classification import classify_links
        from .value_stream_mapping import (
            resolve_theme_value_stream_mapping,
            resolve_value_stream_mapping,
        )

        mapping = resolve_theme_value_stream_mapping(themes, llm_client=llm_client)
        if mapping["value_stream_names"]:
            return mapping

        fallback = resolve_value_stream_mapping(
            {},
            classify_links(issuelinks),
            llm_client=llm_client,
        )
        return {
            "value_stream_names": list(fallback.get("vs_names") or []),
            "value_stream_ids": list(fallback.get("vs_ids") or []),
            "jira_group_ids": list(fallback.get("jira_group_ids") or []),
            "value_stream_statuses": list(fallback.get("vs_statuses") or []),
            "linked_value_streams": list(fallback.get("linked_value_streams") or []),
            "value_stream_label_source": str(fallback.get("label_source") or "jira_issuelinks"),
        }
    except Exception as exc:
        logger.warning("Value-stream resolution failed; returning no fallback labels: %s", exc)
        return {
            "value_stream_names": [],
            "value_stream_ids": [],
            "jira_group_ids": [],
            "value_stream_statuses": [],
            "linked_value_streams": [],
            "value_stream_label_source": "jira_resolution_failed",
        }


def _looks_like_implementation(value: Any) -> bool:
    return "implement" in str(value or "").lower()


def _append_theme(
    themes: List[Dict[str, Any]],
    seen: set[tuple[str, str]],
    issue: Dict[str, Any],
    *,
    source_title: str = "",
) -> None:
    fields = issue.get("fields", {}) or {}
    issue_type = nested_str_field(fields, "issuetype", "name")
    key = str(issue.get("key") or "")
    issue_type_lower = issue_type.lower()
    if "theme" not in issue_type_lower:
        return

    summary_raw = str(fields.get("summary") or "")
    parsed = parse_value_stream_from_theme_title(
        source_title=source_title,
        child_theme_title=summary_raw,
    )
    dedupe_key = (key, summary_raw)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    theme = {
        "key": key,
        "summary": parsed.canonical_name if parsed else clean_value_stream_name(summary_raw),
        "summary_raw": summary_raw,
        "status": nested_str_field(fields, "status", "name"),
        "issue_type": issue_type,
    }
    if parsed:
        theme.update(
            {
                "raw_value_stream_suffix": parsed.raw_value_stream_suffix,
                "value_stream_entity_id": parsed.entity_id,
                "value_stream_match_type": parsed.match_type,
            }
        )
    themes.append(theme)
