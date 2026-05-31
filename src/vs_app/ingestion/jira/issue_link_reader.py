"""Raw reads of Jira issue links.

Pure helpers that pull the issue-link structures out of a Jira issue. They sit
*below* link classification: no Value Stream / Theme resolution and no
categorisation happen here. Classifying links into vs / parent / dependency /
etc. is the job of ``vs_app.ingestion.jira.value_stream_labels`` (and the
ground truth cleanups), which consume the raw links returned here.

Each accepts either a full issue JSON (``{"fields": {...}}``) or a bare fields
dict, so call-sites can pass whichever they already hold.
"""

from __future__ import annotations

from typing import Any


def _fields_of(issue: dict[str, Any]) -> dict[str, Any]:
    """Return the fields dict whether given a full issue or a fields dict."""
    fields = issue.get("fields")
    return fields if isinstance(fields, dict) else issue


def read_issue_links(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the raw ``issuelinks`` list from a Jira issue (or fields dict)."""
    links = _fields_of(issue).get("issuelinks")
    return list(links or [])


def read_linked_issue_keys(issue: dict[str, Any]) -> list[str]:
    """Return the keys of issues linked from this issue, order-preserving.

    Reads the linked issue on each link (outward or inward) and collects its
    key. Duplicates are dropped while keeping first-seen order.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for link in read_issue_links(issue):
        linked = link.get("outwardIssue") or link.get("inwardIssue")
        if not isinstance(linked, dict):
            continue
        key = linked.get("key")
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


__all__ = ["read_issue_links", "read_linked_issue_keys"]
