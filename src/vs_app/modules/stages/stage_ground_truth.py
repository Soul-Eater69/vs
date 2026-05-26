from __future__ import annotations

import re
from typing import Any

from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage
from vs_app.modules.stages.stage_catalog import (
    get_allowed_stages,
    get_value_stream_catalog_entry,
)

BUSINESS_VALUE_STREAM_FIELD = "customfield_18600"
BUSINESS_NEEDS_FIELD = "customfield_20900"

_IDMT_FIELDS = [
    "summary",
    "description",
    "issuetype",
    "issuelinks",
    "attachment",
]
_THEME_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "issuelinks",
    "subtasks",
    BUSINESS_VALUE_STREAM_FIELD,
    BUSINESS_NEEDS_FIELD,
    "customfield_12600",
    "customfield_18602",
    "customfield_18603",
]
_CHILD_FIELDS = ["summary", "status", "issuetype", "parent"]


def parse_business_value_stream(raw: Any) -> dict[str, str]:
    raw_text = _first_text_value(raw)
    name = raw_text
    value_stream_id = ""

    match = re.search(r"\{([^{}]+)\}\s*$", raw_text)
    if match:
        value_stream_id = _clean_text(match.group(1))
        name = _clean_text(raw_text[: match.start()])

    return {
        "raw": raw_text,
        "name": name,
        "id": value_stream_id,
    }


def extract_raw_stage_mentions_from_business_needs(text: str) -> list[dict[str, Any]]:
    cleaned = _strip_markup(_coerce_text(text))
    mentions: list[dict[str, Any]] = []
    pattern = re.compile(r"\b(value\s*stage|stage)\s*[:\-]?\s+([^\n\r]+)", re.I)

    for match in pattern.finditer(cleaned):
        label = match.group(1).lower().replace(" ", "")
        if label == "stage" and not _stage_label_has_business_context(cleaned, match.start()):
            continue

        source_text = _line_containing(cleaned, match.start())
        segment = match.group(2).strip()
        candidates = _split_stage_candidate_segment(segment)
        for candidate in candidates:
            raw_stage = _clean_stage_candidate(candidate)
            if not raw_stage:
                continue
            mentions.append(
                {
                    "raw_stage": raw_stage,
                    "source": "business_needs",
                    "source_text": source_text,
                    "position": match.start(),
                }
            )

    return mentions


async def build_ticket_stage_ground_truth(
    *,
    ticket_key: str,
    jira_client: Any,
    catalog: dict,
    fetch_child_issues: bool = False,
    include_unverified: bool = False,
    llm: Any | None = None,
) -> dict[str, Any]:
    idmt_key = normalize_ticket_key(ticket_key)
    issue = await _fetch_issue(jira_client, idmt_key, fields=_IDMT_FIELDS, expand=True)
    fields = issue.get("fields") or {}
    warnings: list[str] = []

    if not idmt_key.startswith("IDMT-"):
        warnings.append("ticket key does not start with IDMT-")
    issue_type = _issue_type_name(issue)
    if issue_type and issue_type != "Engagement Request":
        warnings.append(f"unexpected IDMT issue type: {issue_type}")

    theme_refs = find_linked_theme_issues(issue)
    linked_themes: list[dict[str, Any]] = []
    gt_by_value_stream: dict[str, list[str]] = {}

    for theme_ref in theme_refs:
        theme_key = str(theme_ref.get("key") or "").strip()
        if not theme_key:
            continue
        theme_issue = await _fetch_issue(jira_client, theme_key, fields=_THEME_FIELDS, expand=True)
        child_issues = await _collect_child_issues(
            jira_client=jira_client,
            theme_issue=theme_issue,
            fetch_child_issues=fetch_child_issues,
        )
        theme_gt = build_theme_stage_ground_truth(
            theme_issue=theme_issue,
            catalog=catalog,
            child_issues=child_issues,
            include_unverified=include_unverified,
            llm=llm,
        )
        linked_themes.append(theme_gt)

        value_stream_name = theme_gt.get("business_value_stream", {}).get("name") or ""
        if not value_stream_name:
            continue
        gt_stages = [
            str(stage.get("canonical") or "").strip()
            for stage in theme_gt.get("verified_stages") or []
            if str(stage.get("canonical") or "").strip()
        ]
        if include_unverified:
            gt_stages.extend(
                str(item.get("raw_stage") or item.get("raw") or "").strip()
                for item in theme_gt.get("unresolved_stage_mentions") or []
                if str(item.get("raw_stage") or item.get("raw") or "").strip()
            )
        gt_by_value_stream[value_stream_name] = _dedupe_preserve(
            list(gt_by_value_stream.get(value_stream_name) or []) + gt_stages
        )

    return {
        "idmt_key": idmt_key,
        "idmt_summary": _clean_text(fields.get("summary")),
        "linked_themes": linked_themes,
        "gt_by_value_stream": gt_by_value_stream,
        "warnings": warnings,
    }


def build_theme_stage_ground_truth(
    *,
    theme_issue: dict[str, Any],
    catalog: dict,
    child_issues: list[dict[str, Any]] | None = None,
    include_unverified: bool = False,
    llm: Any | None = None,
) -> dict[str, Any]:
    fields = theme_issue.get("fields") or {}
    theme_key = _clean_text(theme_issue.get("key"))
    theme_summary = _clean_text(fields.get("summary") or theme_issue.get("summary"))
    business_value_stream = parse_business_value_stream(
        fields.get(BUSINESS_VALUE_STREAM_FIELD)
        or fields.get("businessValueStreams")
        or _value_stream_from_theme_summary(theme_summary)
    )
    value_stream_name = business_value_stream.get("name") or ""

    entry = get_value_stream_catalog_entry(value_stream_name, catalog) or {}
    allowed_stage_defs = list(entry.get("stages") or [])
    allowed_stages = get_allowed_stages(value_stream_name, catalog)
    business_needs_raw = _coerce_text(fields.get(BUSINESS_NEEDS_FIELD))
    warnings: list[str] = []
    if value_stream_name and not allowed_stage_defs:
        warnings.append(f"no approved stage catalog entry for value stream: {value_stream_name}")

    raw_mentions = extract_raw_stage_mentions_from_business_needs(business_needs_raw)
    child_issue_rows: list[dict[str, Any]] = []
    for child in child_issues or []:
        child_row = _child_issue_row(child)
        child_issue_rows.append(child_row)
        raw_mentions.extend(
            extract_raw_stage_mentions_from_child_issue(
                child,
                theme_summary=theme_summary,
                value_stream_name=value_stream_name,
            )
        )

    by_canonical: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    for mention in raw_mentions:
        raw_stage = str(mention.get("raw_stage") or "").strip()
        result = canonicalize_stage(
            raw_stage,
            allowed_stage_defs or allowed_stages,
            llm=llm,
            value_stream_name=value_stream_name,
        )
        raw_payload = _raw_mention_payload(mention)
        canonical = result.get("canonical")
        if canonical:
            stage = by_canonical.setdefault(
                str(canonical),
                {
                    "canonical": str(canonical),
                    "confidence": result.get("confidence", 0.0),
                    "match_method": result.get("match_method", ""),
                    "raw_mentions": [],
                },
            )
            stage["raw_mentions"].append(raw_payload)
            if float(result.get("confidence") or 0.0) > float(stage.get("confidence") or 0.0):
                stage["confidence"] = result.get("confidence", 0.0)
                stage["match_method"] = result.get("match_method", "")
            elif result.get("match_method") == "exact":
                stage["match_method"] = "exact"
            continue

        unresolved_item = dict(raw_payload)
        unresolved_item["raw_stage"] = raw_stage
        unresolved_item["match_method"] = result.get("match_method", "unresolved")
        unresolved_item["confidence"] = result.get("confidence", 0.0)
        unresolved_item["warnings"] = list(result.get("warnings") or [])
        unresolved.append(unresolved_item)

    verified_stages = sorted(by_canonical.values(), key=lambda item: item["canonical"])
    if include_unverified:
        warnings.append("include_unverified is set; unresolved raw stages may be included in gt_by_value_stream")

    return {
        "theme_key": theme_key,
        "theme_summary": theme_summary,
        "business_value_stream": business_value_stream,
        "allowed_stages": allowed_stages,
        "business_needs_raw": business_needs_raw,
        "verified_stages": verified_stages,
        "unresolved_stage_mentions": unresolved,
        "child_issues": child_issue_rows,
        "warnings": warnings,
    }


def find_linked_theme_issues(issue: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    fields = issue.get("fields") or {}
    for link in fields.get("issuelinks") or []:
        link_type = link.get("type") or {}
        link_type_text = " ".join(
            str(link_type.get(key) or "")
            for key in ("name", "outward", "inward")
        ).lower()
        if "implement" not in link_type_text:
            continue
        for side in ("outwardIssue", "inwardIssue"):
            linked = link.get(side)
            if not isinstance(linked, dict):
                continue
            key = _clean_text(linked.get("key"))
            if not key.startswith("GROUP-") or key in seen:
                continue
            issue_type = _issue_type_name(linked)
            if issue_type and issue_type.lower() != "theme":
                continue
            seen.add(key)
            out.append(
                {
                    "key": key,
                    "summary": _clean_text((linked.get("fields") or {}).get("summary")),
                    "issue_type": issue_type,
                }
            )
    return out


def extract_raw_stage_mentions_from_child_issue(
    child_issue: dict[str, Any],
    *,
    theme_summary: str = "",
    value_stream_name: str = "",
) -> list[dict[str, Any]]:
    summary = _clean_text((child_issue.get("fields") or {}).get("summary") or child_issue.get("summary"))
    if not summary:
        return []

    candidate = summary
    if " - " in candidate:
        candidate = candidate.rsplit(" - ", 1)[-1]
    elif value_stream_name and value_stream_name.lower() in candidate.lower():
        idx = candidate.lower().find(value_stream_name.lower())
        candidate = candidate[idx + len(value_stream_name) :]
    elif " : " in candidate:
        candidate = candidate.rsplit(" : ", 1)[-1]
    elif ":" in candidate:
        candidate = candidate.rsplit(":", 1)[-1]

    candidate = _clean_stage_candidate(candidate)
    if not candidate or candidate == _clean_stage_candidate(theme_summary):
        return []

    return [
        {
            "raw_stage": candidate,
            "source": "child_issue_summary",
            "source_text": summary,
            "child_key": _clean_text(child_issue.get("key")),
        }
    ]


async def _collect_child_issues(
    *,
    jira_client: Any,
    theme_issue: dict[str, Any],
    fetch_child_issues: bool,
) -> list[dict[str, Any]]:
    fields = theme_issue.get("fields") or {}
    by_key: dict[str, dict[str, Any]] = {}

    for child in fields.get("subtasks") or []:
        hydrated = await _maybe_hydrate_child_issue(jira_client, child)
        key = _clean_text(hydrated.get("key") or child.get("key"))
        if key:
            by_key[key] = hydrated

    if fetch_child_issues:
        theme_key = _clean_text(theme_issue.get("key"))
        result = await _search_issues(
            jira_client,
            f"parent = {theme_key}",
            fields=_CHILD_FIELDS,
        )
        for child in result.get("issues") or []:
            key = _clean_text(child.get("key"))
            if key and key not in by_key:
                by_key[key] = child

    return sorted(by_key.values(), key=lambda item: _clean_text(item.get("key")))


async def _maybe_hydrate_child_issue(jira_client: Any, child: dict[str, Any]) -> dict[str, Any]:
    fields = child.get("fields") or {}
    if fields.get("summary") and fields.get("issuetype"):
        return child
    key = _clean_text(child.get("key"))
    if not key:
        return child
    try:
        return await _fetch_issue(jira_client, key, fields=_CHILD_FIELDS, expand=False)
    except Exception:
        return child


async def _fetch_issue(
    jira_client: Any,
    issue_key: str,
    *,
    fields: list[str],
    expand: bool,
) -> dict[str, Any]:
    get_issue = getattr(jira_client, "get_issue", None)
    if callable(get_issue):
        return dict(await get_issue(issue_key, fields=fields, expand=expand))

    get_issue_by_key = getattr(jira_client, "get_issue_by_key", None)
    if callable(get_issue_by_key):
        return dict(await get_issue_by_key(issue_key, fields=fields))

    nested_client = getattr(jira_client, "client", None)
    nested_get_issue = getattr(nested_client, "get_issue_by_key", None)
    if callable(nested_get_issue):
        return dict(await nested_get_issue(issue_key, fields=fields))

    raise TypeError("jira_client must provide get_issue() or get_issue_by_key()")


async def _search_issues(jira_client: Any, jql: str, *, fields: list[str]) -> dict[str, Any]:
    search_issues = getattr(jira_client, "search_issues", None)
    if callable(search_issues):
        return dict(await search_issues(jql, start_at=0, max_results=100, fields=fields))

    get_issues = getattr(jira_client, "get_issues", None)
    if callable(get_issues):
        return dict(await get_issues(jql=jql, start_at=0, max_results=100, fields=fields))

    nested_client = getattr(jira_client, "client", None)
    nested_get_issues = getattr(nested_client, "get_issues", None)
    if callable(nested_get_issues):
        return dict(await nested_get_issues(jql=jql, start_at=0, max_results=100, fields=fields))

    return {"issues": [], "total": 0}


def _split_stage_candidate_segment(segment: str) -> list[str]:
    segment = segment.strip()
    if "|" in segment:
        return [segment.split("|", 1)[0]]
    if ";" in segment:
        return [part for part in segment.split(";") if part.strip()]
    return [segment]


def _clean_stage_candidate(value: Any) -> str:
    text = _strip_markup(str(value or ""))
    text = re.sub(r"\[[^\]]+\|([^\]]+)\]", r"\1", text)
    text = text.split("\n", 1)[0]
    text = text.strip(" -*:\t\r\n")
    text = re.sub(r"\s+", " ", text)
    return text


def _stage_label_has_business_context(text: str, position: int) -> bool:
    window = text[max(0, position - 80) : position + 80].lower()
    return any(token in window for token in ("business need", "value stage", "stage:"))


def _line_containing(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    if end == -1:
        end = len(text)
    return _clean_text(text[start:end])


def _raw_mention_payload(mention: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "raw": str(mention.get("raw_stage") or "").strip(),
        "source": str(mention.get("source") or "").strip(),
    }
    for key in ("source_text", "position", "child_key"):
        if mention.get(key) is not None:
            payload[key] = mention[key]
    return payload


def _child_issue_row(issue: dict[str, Any]) -> dict[str, str]:
    fields = issue.get("fields") or {}
    return {
        "key": _clean_text(issue.get("key")),
        "summary": _clean_text(fields.get("summary") or issue.get("summary")),
        "status": _status_name(issue),
        "issue_type": _issue_type_name(issue),
    }


def _issue_type_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    issue_type = fields.get("issuetype") or issue.get("issueType") or {}
    if isinstance(issue_type, dict):
        return _clean_text(issue_type.get("name"))
    return _clean_text(issue_type)


def _status_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    status = fields.get("status") or issue.get("status") or {}
    if isinstance(status, dict):
        return _clean_text(status.get("name"))
    return _clean_text(status)


def _value_stream_from_theme_summary(summary: str) -> str:
    if " : " in summary:
        return summary.rsplit(" : ", 1)[-1].strip()
    if " - " in summary:
        return summary.rsplit(" - ", 1)[-1].strip()
    return summary


def _first_text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "text"):
            if value.get(key):
                return _clean_text(value.get(key))
        return _clean_text(_coerce_text(value))
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = _first_text_value(item)
            if text:
                return text
        return ""
    return _clean_text(value)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "content" in value:
            return " ".join(_adf_text(value))
        for key in ("value", "text", "name"):
            if value.get(key):
                return str(value.get(key))
        return " ".join(_coerce_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_coerce_text(item) for item in value)
    return str(value)


def _adf_text(node: Any) -> list[str]:
    if isinstance(node, dict):
        out: list[str] = []
        if node.get("type") == "text" and node.get("text"):
            out.append(str(node.get("text")))
        for child in node.get("content") or []:
            out.extend(_adf_text(child))
        return out
    if isinstance(node, list):
        out: list[str] = []
        for item in node:
            out.extend(_adf_text(item))
        return out
    return []


def _strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\{[^{}]+\}", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _dedupe_preserve(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def normalize_ticket_key(value: Any) -> str:
    return _clean_text(value).upper()


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "BUSINESS_NEEDS_FIELD",
    "BUSINESS_VALUE_STREAM_FIELD",
    "build_theme_stage_ground_truth",
    "build_ticket_stage_ground_truth",
    "extract_raw_stage_mentions_from_business_needs",
    "extract_raw_stage_mentions_from_child_issue",
    "find_linked_theme_issues",
    "normalize_ticket_key",
    "parse_business_value_stream",
]
