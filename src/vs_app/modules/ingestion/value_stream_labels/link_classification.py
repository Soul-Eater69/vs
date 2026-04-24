"""Classify Jira issue links into semantic categories."""

from __future__ import annotations

import re
from typing import Any

from .helpers import clean_value_stream_name

# ---------------------------------------------------------------------------
# Link type -> category mapping
# ---------------------------------------------------------------------------

LINK_TYPE_MAP: dict[str, str] = {
    # Value stream links (ground truth - goes to supervision view)
    "value stream": "vs",
    "value-stream": "vs",
    "vs link": "vs",
    "implements value stream": "vs",
    # Product / System Links
    "product": "product",
    "affects product": "product",
    "impacts system": "product",
    # Dependency links
    "depends on": "dependency",
    "blocks": "dependency",
    "is blocked by": "dependency",
    "blocks": "dependency",
    "is blocked by": "dependency",
    "depends on": "dependency",
    # Hierarchy links
    "epic link": "parent",
    "parent": "parent",
    "sub-task": "parent",
    "is child of": "parent",
    "is parent of": "parent",
    # Related Ideas
    "relates to": "related",
    "relates to": "related",
    "duplicates": "related",
    "duplicates": "related",
    "clones": "related",
    "clones": "related",
    # Implementation links
    "implements": "implementation",
    "story link": "implementation",
    "is implemented by": "implementation",
}

_VS_TOKEN_RE = re.compile(r"\bvalue[\s-]*stream\b|\bvs\b", re.IGNORECASE)


def classify_links(issue_links: list[dict]) -> dict[str, list[dict]]:
    """Classify Jira issue links into semantic categories.

    Returns a dict keyed by category: vs, product, dependency, parent,
    related, implementation, unknown.
    """
    classified: dict[str, list[dict]] = {
        "vs": [],
        "product": [],
        "dependency": [],
        "parent": [],
        "related": [],
        "implementation": [],
        "unknown": [],
    }

    for link in issue_links or []:
        linked = link.get("outwardIssue") or link.get("inwardIssue")
        if not linked:
            continue

        link_type = link.get("type") or {}
        link_type_name = str(link_type.get("name") or "")
        category = _categorize_link_type(link_type)

        raw_summary = (linked.get("fields") or {}).get("summary", "")
        entry: dict[str, Any] = {
            "type": link_type_name,
            "category": category,
            "direction": "outward" if link.get("outwardIssue") else "inward",
            "key": linked.get("key", ""),
            "summary": clean_value_stream_name(raw_summary) if category == "vs" else raw_summary,
            "summary_raw": raw_summary,
            "project": linked.get("key", "-").split("-")[0],
            "status": ((linked.get("fields") or {}).get("status") or {}).get("name", ""),
        }
        classified[category].append(entry)

    return classified


def _categorize_link_type(link_type: dict[str, Any]) -> str:
    descriptors = [
        str(link_type.get("name") or "").strip(),
        str(link_type.get("outward") or "").strip(),
        str(link_type.get("inward") or "").strip(),
    ]
    normalized = [value.lower() for value in descriptors if value]

    for value in normalized:
        category = LINK_TYPE_MAP.get(value)
        if category:
            return category

    joined = " | ".join(normalized)
    if _VS_TOKEN_RE.search(joined):
        return "vs"
    if "implement" in joined:
        return "implementation"
    if any(token in joined for token in ("epic", "parent", "child", "sub-task")):
        return "parent"
    if any(token in joined for token in ("block", "depend")):
        return "dependency"
    if any(token in joined for token in ("relate", "duplicate", "clone")):
        return "related"
    if "product" in joined or "system" in joined:
        return "product"
    return "unknown"
