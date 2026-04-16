"""Classify Jira issue links into semantic categories."""

from __future__ import annotations

from typing import Any

from jira.value_stream.helpers import clean_value_stream_name

# ---------------------------------------------------------------------------
# Link type -> category mapping
# ---------------------------------------------------------------------------

LINK_TYPE_MAP: dict[str, str] = {
    # Value stream links (ground truth - goes to supervision view)
    "Value Stream": "vs",
    "value-stream": "vs",
    "VS Link": "vs",
    "Implements Value Stream": "vs",
    # Product / System Links
    "Product": "product",
    "Affects Product": "product",
    "Impacts System": "product",
    # Dependency links
    "Depends On": "dependency",
    "Blocks": "dependency",
    "Is Blocked By": "dependency",
    "blocks": "dependency",
    "is blocked by": "dependency",
    "depends on": "dependency",
    # Hierarchy links
    "Epic Link": "parent",
    "Parent": "parent",
    "Sub-task": "parent",
    "is child of": "parent",
    "is parent of": "parent",
    # Related Ideas
    "Relates To": "related",
    "relates to": "related",
    "Duplicates": "related",
    "duplicates": "related",
    "Clones": "related",
    "clones": "related",
    # Implementation links
    "Implements": "implementation",
    "Story Link": "implementation",
    "is implemented by": "implementation",
}


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

        link_type_name = (link.get("type") or {}).get("name", "")
        category = LINK_TYPE_MAP.get(link_type_name, "unknown")

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
