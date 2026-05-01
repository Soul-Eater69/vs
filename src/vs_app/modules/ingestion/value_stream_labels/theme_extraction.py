"""Extract themes (value streams) from Jira issue links."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .helpers import clean_value_stream_name, nested_str_field, str_field

logger = logging.getLogger(__name__)


def extract_themes(issuelinks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract themes from implementation-style issue links.

    Returns a list of theme dicts with keys: key, summary, summary_raw, status.
    """
    themes: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for link in issuelinks:
        link_type = link.get("type", {}) or {}

        if _looks_like_implementation(link_type.get("outward")) and "outwardIssue" in link:
            _append_theme(themes, seen, link["outwardIssue"])

        if _looks_like_implementation(link_type.get("inward")) and "inwardIssue" in link:
            _append_theme(themes, seen, link["inwardIssue"])

    return themes


def resolve_value_streams(
    themes: List[Dict[str, Any]],
    issuelinks: List[Dict[str, Any]],
    llm_client: Any | None = None,
) -> Dict[str, Any]:
    """Resolve canonical value stream names from classified value-stream links.

    Returns dict with keys: value_stream_names, value_stream_ids,
    value_stream_statuses, value_stream_label_source.
    Linked themes are no longer used as a value-stream fallback.
    """
    try:
        from .link_classification import classify_links
        from .value_stream_mapping import resolve_value_stream_mapping

        classified_links = classify_links(issuelinks)
        vs_mapping = resolve_value_stream_mapping(
            {},
            classified_links,
            llm_client=llm_client,
        )
        return {
            "value_stream_names": list(vs_mapping.get("vs_names") or []),
            "value_stream_ids": list(vs_mapping.get("vs_ids") or []),
            "value_stream_statuses": list(vs_mapping.get("vs_statuses") or []),
            "linked_value_streams": list(vs_mapping.get("linked_value_streams") or []),
            "value_stream_label_source": str(vs_mapping.get("label_source") or "jira_issuelinks"),
        }
    except Exception as exc:
        logger.warning("Value-stream resolution failed; returning no fallback labels: %s", exc)
        return {
            "value_stream_names": [],
            "value_stream_ids": [],
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
) -> None:
    fields = issue.get("fields", {}) or {}
    issue_type = nested_str_field(fields, "issuetype", "name")
    if "theme" not in issue_type.lower():
        return

    key = str(issue.get("key") or "")
    summary_raw = str(fields.get("summary") or "")
    dedupe_key = (key, summary_raw)
    if dedupe_key in seen:
        return
    seen.add(dedupe_key)
    themes.append({
        "key": key,
        "summary": clean_value_stream_name(summary_raw),
        "summary_raw": summary_raw,
        "status": nested_str_field(fields, "status", "name"),
        "issue_type": issue_type,
    })
