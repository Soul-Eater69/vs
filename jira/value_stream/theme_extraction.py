"""Extract themes (value streams) from Jira issue links."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from jira.value_stream.helpers import clean_value_stream_name, nested_str_field, str_field

logger = logging.getLogger(__name__)


def extract_themes(issuelinks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract themes from "Implements" / "is implemented by" issue links.

    Returns a list of theme dicts with keys: key, summary, summary_raw, status.
    """
    themes: List[Dict[str, Any]] = []

    for link in issuelinks:
        link_type = link.get("type", {})

        if link_type.get("outward") == "Implements" and "outwardIssue" in link:
            fields = link["outwardIssue"].get("fields", {})
            summary_raw = fields.get("summary") or ""
            themes.append({
                "key": link["outwardIssue"].get("key"),
                "summary": clean_value_stream_name(summary_raw),
                "summary_raw": summary_raw,
                "status": nested_str_field(fields, "status", "name"),
            })

        if link_type.get("inward") == "is implemented by" and "inwardIssue" in link:
            fields = link["inwardIssue"].get("fields", {})
            summary_raw = fields.get("summary") or ""
            themes.append({
                "key": link["inwardIssue"].get("key"),
                "summary": clean_value_stream_name(summary_raw),
                "summary_raw": summary_raw,
                "status": nested_str_field(fields, "status", "name"),
            })

    return themes


def resolve_value_streams(
    themes: List[Dict[str, Any]],
    issuelinks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve canonical value stream names from themes + classified links.

    Returns dict with keys: value_stream_names, value_stream_ids,
    value_stream_statuses, value_stream_label_source.
    Falls back to themes-only representation on import/resolution failure.
    """
    try:
        from jira.value_stream.link_classification import classify_links
        from jira.value_stream.value_stream_mapping import resolve_value_stream_mapping

        classified_links = classify_links(issuelinks)
        vs_mapping = resolve_value_stream_mapping({"themes": themes}, classified_links)
        return {
            "value_stream_names": list(vs_mapping.get("vs_names") or []),
            "value_stream_ids": list(vs_mapping.get("vs_ids") or []),
            "value_stream_statuses": list(vs_mapping.get("vs_statuses") or []),
            "linked_value_streams": list(vs_mapping.get("linked_value_streams") or []),
            "value_stream_label_source": str(vs_mapping.get("label_source") or "jira_issuelinks"),
        }
    except Exception:
        value_streams = [
            {
                "id": str_field(theme, "key"),
                "name": str_field(theme, "summary"),
                "status": str_field(theme, "status"),
            }
            for theme in themes
        ]
        return {
            "value_stream_names": [v["name"] for v in value_streams if v["name"]],
            "value_stream_ids": [v["id"] for v in value_streams if v["id"]],
            "value_stream_statuses": [v["status"] for v in value_streams],
            "value_stream_label_source": "jira_themes_fallback",
        }
