"""Extract and map epics from Jira issue data."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from jira.value_stream.helpers import clean_value_stream_name, nested_str_field, str_field


# ---------------------------------------------------------------------------
# Epic extraction
# ---------------------------------------------------------------------------

def extract_epics(issue: Dict[str, Any], config: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Extract epics from a Jira issue by checking custom fields, parent, links, and subtasks.

    Returns a deduplicated list of epic dicts with keys:
    key, summary, summary_raw, status, source, issue_type.
    """
    epics: List[Dict[str, Any]] = []
    fields = issue.get("fields", {})

    jira_field_map: dict[str, str] = getattr(config, "jira_field_map", {}) if config else {}
    epic_field_id = jira_field_map.get("epic_link", "customfield_10014")

    def _append(key: str, summary_raw: str, status: str, source: str, issue_type: str = "") -> None:
        cleaned_key = str(key or "").strip()
        cleaned_raw = str(summary_raw or "").strip()
        if not cleaned_key and not cleaned_raw:
            return
        epics.append({
            "key": cleaned_key,
            "summary": clean_value_stream_name(cleaned_raw),
            "summary_raw": cleaned_raw,
            "status": str(status or "").strip(),
            "source": source,
            "issue_type": str(issue_type or "").strip(),
        })

    # 1) Explicit epic link custom field
    epic_link_value = fields.get(epic_field_id)
    if isinstance(epic_link_value, str) and epic_link_value.strip():
        _append(epic_link_value, "", "", "customfield")
    elif isinstance(epic_link_value, dict):
        _append(
            key=str_field(epic_link_value, "key"),
            summary_raw=str_field(epic_link_value, "summary"),
            status=nested_str_field(epic_link_value, "status", "name"),
            source="customfield",
            issue_type=nested_str_field(epic_link_value, "issuetype", "name"),
        )

    # 2) Parent field (next-gen Jira)
    parent = fields.get("parent")
    if isinstance(parent, dict):
        parent_fields = parent.get("fields", {})
        _append(
            key=str_field(parent, "key"),
            summary_raw=str_field(parent_fields, "summary"),
            status=nested_str_field(parent_fields, "status", "name"),
            source="parent",
            issue_type=nested_str_field(parent_fields, "issuetype", "name"),
        )

    # 3) Issue links with epic/hierarchy relationships
    for link in fields.get("issuelinks", []):
        link_type = link.get("type", {}) or {}
        link_name = str(link_type.get("name") or "").lower()
        outward = str(link_type.get("outward") or "").lower()
        inward = str(link_type.get("inward") or "").lower()

        for direction in ("outwardIssue", "inwardIssue"):
            linked_issue = link.get(direction)
            if not isinstance(linked_issue, dict):
                continue

            linked_fields = linked_issue.get("fields", {})
            issue_type = nested_str_field(linked_fields, "issuetype", "name").lower()
            key = str_field(linked_issue, "key")

            is_hierarchy = (
                "epic" in link_name
                or "parent" in link_name
                or "child" in link_name
                or outward in ("is child of", "has epic", "parent")
                or inward in ("is parent of", "is epic of", "child")
            )
            likely_epic = "epic" in issue_type or "epic" in link_name

            if (likely_epic or is_hierarchy) and (likely_epic or key):
                _append(
                    key=key,
                    summary_raw=str_field(linked_fields, "summary"),
                    status=nested_str_field(linked_fields, "status", "name"),
                    source="link",
                    issue_type=issue_type,
                )

    # 4) Subtasks (for GROUP tickets, children ARE the epics)
    for subtask in fields.get("subtasks") or []:
        if not isinstance(subtask, dict):
            continue
        sub_fields = subtask.get("fields") or {}
        _append(
            key=str_field(subtask, "key"),
            summary_raw=str_field(sub_fields, "summary"),
            status=nested_str_field(sub_fields, "status", "name"),
            source="subtask",
            issue_type=nested_str_field(sub_fields, "issuetype", "name"),
        )

    return _dedupe_epics(epics)


def _dedupe_epics(epics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate epics by key first, then by normalized summary."""
    deduped: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_names: set[str] = set()

    for epic in epics:
        key = str_field(epic, "key").lower()
        name = (str_field(epic, "summary") or str_field(epic, "summary_raw")).lower()

        if key and key in seen_keys:
            continue
        if not key and name and name in seen_names:
            continue

        if key:
            seen_keys.add(key)
        if name:
            seen_names.add(name)
        deduped.append(epic)

    return deduped


# ---------------------------------------------------------------------------
# Value stream <-> epic mapping
# ---------------------------------------------------------------------------

def _norm_name(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def map_value_streams_to_epics(
    value_streams: List[str],
    epics: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map ticket epics to value streams using normalized name similarity.

    For each epic, finds the best matching value stream (sequence similarity
    or token overlap). Assigns if score >= 0.40, or if only one VS exists.
    """
    if not value_streams:
        return []

    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    for vs in value_streams:
        vs_id = str(vs.get("id") or "").strip()
        vs_name = str(vs.get("name") or "").strip()
        bucket_key = vs_id or vs_name
        if not bucket_key or bucket_key in buckets:
            continue

        buckets[bucket_key] = {
            "value_stream": {
                "id": vs_id,
                "name": vs_name,
                "status": str(vs.get("status") or "").strip(),
            },
            "epics": [],
            "epic_ids": [],
            "epic_count": 0,
        }
        order.append(bucket_key)

    if not buckets:
        return []

    for epic in epics:
        epic_name = str_field(epic, "summary") or str_field(epic, "summary_raw")
        epic_key = str_field(epic, "key")
        epic_norm = _norm_name(epic_name)

        best_bucket = None
        best_score = 0.0

        for bucket_key in order:
            vs_name = str(buckets[bucket_key]["value_stream"].get("name") or "").strip()
            vs_norm = _norm_name(vs_name)
            if not vs_norm and not epic_norm:
                continue

            seq_score = SequenceMatcher(None, epic_norm, vs_norm).ratio() if (epic_norm and vs_norm) else 0.0
            epic_tokens = set(epic_norm.split()) if epic_norm else set()
            vs_tokens = set(vs_norm.split()) if vs_norm else set()
            overlap = len(epic_tokens & vs_tokens) / max(len(vs_tokens), 1) if vs_tokens else 0.0
            score = max(seq_score, overlap)

            if score > best_score:
                best_score = score
                best_bucket = bucket_key

        assign_bucket = None
        if best_bucket and best_score >= 0.40:
            assign_bucket = best_bucket
        elif len(order) == 1:
            assign_bucket = order[0]

        if assign_bucket is None:
            continue

        bucket = buckets[assign_bucket]
        if epic_key and any(str_field(e, "key") == epic_key for e in bucket["epics"]):
            continue
        bucket["epics"].append(epic)
        if epic_key:
            bucket["epic_ids"].append(epic_key)

    result: List[Dict[str, Any]] = []
    for bucket_key in order:
        bucket = buckets[bucket_key]
        bucket["epic_count"] = len(bucket["epics"])
        result.append(bucket)

    return result
