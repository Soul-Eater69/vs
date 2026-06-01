from __future__ import annotations

import re
from typing import Any

from vs_app.ingestion.jira.value_stream_labels import (
    extract_themes,
    resolve_value_streams,
)
# NOTE: stage_canonicalizer and stage_catalog are imported lazily inside the
# functions that use them. A module-level import would create a circular import
# (this module <- modules.stages package __init__ <- the compatibility shim <-
# this module), so the imports are deferred to call time. Behavior is unchanged.

BUSINESS_NEEDS_FIELD = "customfield_20900"
PARENT_LINK_FIELD = "customfield_11401"

IDMT_FIELDS = [
    "summary",
    "description",
    "issuetype",
    "issuelinks",
    "attachment",
]
THEME_FIELDS = [
    "summary",
    "description",
    "status",
    "issuetype",
    "issuelinks",
    "subtasks",
    BUSINESS_NEEDS_FIELD,
    "customfield_12600",
    "customfield_18602",
    "customfield_18603",
]
CHILD_EPIC_FIELDS = [
    "summary",
    "status",
    "issuetype",
    "parent",
    PARENT_LINK_FIELD,
]


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


def business_value_stream_from_resolved_link(
    resolved_value_stream: dict[str, Any] | None,
    *,
    theme_summary: str = "",
) -> dict[str, str]:
    if resolved_value_stream and resolved_value_stream.get("name"):
        return {
            "raw": str(
                resolved_value_stream.get("summary_raw")
                or resolved_value_stream.get("name")
                or ""
            ),
            "name": str(resolved_value_stream.get("name") or ""),
            "id": str(resolved_value_stream.get("id") or ""),
            "source": str(
                resolved_value_stream.get("source") or "jira_value_stream_labels"
            ),
        }

    fallback_name = _value_stream_from_theme_summary(theme_summary)
    if fallback_name:
        return {
            "raw": theme_summary,
            "name": fallback_name,
            "id": "",
            "source": "theme_summary_fallback",
        }

    return {"raw": "", "name": "", "id": "", "source": "unresolved"}


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
) -> dict[str, Any]:
    idmt_key = normalize_ticket_key(ticket_key)
    issue = await _fetch_issue(jira_client, idmt_key, fields=IDMT_FIELDS, expand=True)
    fields = issue.get("fields") or {}
    warnings: list[str] = []

    if not idmt_key.startswith("IDMT-"):
        warnings.append("ticket key does not start with IDMT-")
    issue_type = _issue_type_name(issue)
    if issue_type and issue_type != "Engagement Request":
        warnings.append(f"unexpected IDMT issue type: {issue_type}")

    idmt_summary = _clean_text(fields.get("summary"))
    issuelinks = list(fields.get("issuelinks") or [])
    theme_refs = extract_themes(
        issuelinks,
        source_title=idmt_summary,
    )
    value_stream_resolution = resolve_value_streams(
        theme_refs,
        issuelinks,
        llm_client=None,
    )
    linked_vs_by_group_key = {
        normalize_ticket_key(row.get("jira_group_id")): row
        for row in value_stream_resolution.get("linked_value_streams") or []
        if normalize_ticket_key(row.get("jira_group_id"))
    }
    linked_themes: list[dict[str, Any]] = []
    gt_by_value_stream: dict[str, list[str]] = {}

    for theme_ref in theme_refs:
        theme_key = normalize_ticket_key(theme_ref.get("key"))
        if not theme_key:
            continue
        linked_vs = linked_vs_by_group_key.get(theme_key)
        theme_issue = await _fetch_issue(jira_client, theme_key, fields=THEME_FIELDS, expand=True)
        child_epics, child_lookup_debug = await fetch_direct_child_epics(
            jira_client=jira_client,
            theme_key=theme_key,
        )
        theme_gt = build_theme_stage_ground_truth(
            theme_issue=theme_issue,
            catalog=catalog,
            child_epics=child_epics,
            child_lookup_debug=child_lookup_debug,
            resolved_value_stream=linked_vs,
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
        if not gt_stages:
            continue
        gt_by_value_stream[value_stream_name] = _dedupe_preserve(
            list(gt_by_value_stream.get(value_stream_name) or []) + gt_stages
        )

    return {
        "idmt_key": idmt_key,
        "idmt_summary": idmt_summary,
        "idmt_description": _clean_text(_coerce_text(fields.get("description"))),
        "value_stream_resolution": value_stream_resolution,
        "linked_themes": linked_themes,
        "gt_by_value_stream": gt_by_value_stream,
        "warnings": warnings,
    }


def _unresolved_stage_mention(
    *,
    resolution: dict[str, Any],
    source: str,
    source_type: str,
    epic_key: str,
    theme_key: str,
    value_stream_name: str,
    allowed_stages: list[Any],
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Build a diagnostic record for a stage mention that failed canonicalization.

    Diagnostics only: this never affects which stages are verified or which
    enter gt_by_value_stream. It captures enough context to analyse stage GT
    false negatives later -- the failed candidates, the reason, the allowed
    catalog stages, and the Epic's discovery source.
    """
    warnings = list(resolution.get("warnings") or [])
    allowed_stage_names = [
        stage.get("name") if isinstance(stage, dict) else _clean_text(stage)
        for stage in allowed_stages
    ]
    record: dict[str, Any] = {
        "raw_stage": resolution.get("cleaned_stage_name") or "",
        "source": source,
        "source_type": source_type,
        "epic_key": epic_key,
        "epic_summary": resolution.get("raw_summary") or "",
        "source_text": resolution.get("raw_summary") or "",
        "theme_key": theme_key,
        "value_stream_name": value_stream_name,
        "included": False,
        "candidates": list(resolution.get("candidates") or []),
        "match_method": resolution.get("match_method"),
        "confidence": resolution.get("confidence"),
        "reason": "; ".join(warnings)
        or "stage candidates did not match the approved stage catalog",
        "allowed_stage_count": len(allowed_stages),
        "allowed_stage_names": allowed_stage_names,
        "warnings": warnings,
    }
    record.update(extra)
    return record


def build_theme_stage_ground_truth(
    *,
    theme_issue: dict[str, Any],
    child_epics: list[dict[str, Any]] | None = None,
    child_lookup_debug: dict[str, Any] | None = None,
    catalog: dict,
    resolved_value_stream: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = theme_issue.get("fields") or {}
    theme_key = _clean_text(theme_issue.get("key"))
    theme_summary = _clean_text(fields.get("summary") or theme_issue.get("summary"))
    business_value_stream = business_value_stream_from_resolved_link(
        resolved_value_stream,
        theme_summary=theme_summary,
    )
    value_stream_name = business_value_stream.get("name") or ""

    from vs_app.modules.stages.stage_catalog import (
        get_allowed_stages,
        get_value_stream_catalog_entry,
    )

    entry = get_value_stream_catalog_entry(value_stream_name, catalog) or {}
    allowed_stage_defs = list(entry.get("stages") or [])
    allowed_stages = get_allowed_stages(value_stream_name, catalog)
    business_needs_raw = _coerce_text(fields.get(BUSINESS_NEEDS_FIELD))
    warnings: list[str] = []
    if value_stream_name and not allowed_stage_defs:
        warnings.append(f"no approved stage catalog entry for value stream: {value_stream_name}")

    business_needs_mentions_debug_only = extract_raw_stage_mentions_from_business_needs(
        business_needs_raw
    )
    allowed_stage_options = allowed_stage_defs or allowed_stages
    linked_epics = extract_epic_links_from_theme_issue(theme_issue)
    linked_epic_stage_resolution_debug: list[dict[str, Any]] = []
    linked_epic_mentions_debug: list[dict[str, Any]] = []
    child_issue_stage_resolution_debug: list[dict[str, Any]] = []
    child_issue_mentions_debug: list[dict[str, Any]] = []
    canonicalization_debug: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    by_canonical: dict[str, dict[str, Any]] = {}

    for linked_epic in linked_epics:
        resolution = resolve_linked_epic_stage(
            linked_issue=linked_epic,
            allowed_stages=allowed_stage_options,
            theme_summary=theme_summary,
            value_stream_name=value_stream_name,
        )
        linked_epic_stage_resolution_debug.append(resolution)
        if not resolution.get("included"):
            unresolved.append(
                _unresolved_stage_mention(
                    resolution=resolution,
                    source="theme_issue_link_epic_summary",
                    source_type="linked_epic",
                    epic_key=resolution.get("linked_issue_key") or "",
                    theme_key=theme_key,
                    value_stream_name=value_stream_name,
                    allowed_stages=allowed_stage_options,
                    extra={
                        "linked_issue_key": resolution.get("linked_issue_key") or "",
                        "link_source": linked_epic.get("link_source"),
                        "link_type": linked_epic.get("link_type"),
                        "link_direction": linked_epic.get("link_direction"),
                    },
                )
            )
            continue

        mention = {
            "raw_stage": resolution.get("cleaned_stage_name") or "",
            "source": "theme_issue_link_epic_summary",
            "source_text": resolution.get("raw_summary") or "",
            "linked_issue_key": resolution.get("linked_issue_key") or "",
            "theme_key": theme_key,
        }
        linked_epic_mentions_debug.append(mention)
        canonicalization_debug.append(
            {
                "raw_stage": resolution.get("cleaned_stage_name") or "",
                "source": "theme_issue_link_epic_summary",
                "linked_issue_key": resolution.get("linked_issue_key") or "",
                "theme_key": theme_key,
                "source_text": resolution.get("raw_summary") or "",
                "canonical": resolution.get("canonical_stage"),
                "match_method": resolution.get("match_method"),
                "confidence": resolution.get("confidence"),
                "warnings": list(resolution.get("warnings") or []),
            }
        )
        canonical = resolution.get("canonical_stage")
        if canonical:
            add_verified_stage(
                by_canonical,
                canonical=str(canonical),
                confidence=float(resolution.get("confidence") or 0.0),
                match_method=str(resolution.get("match_method") or ""),
                raw_payload=_raw_mention_payload(mention),
            )

    child_issue_rows: list[dict[str, Any]] = []
    for child in child_epics or []:
        child_row = _child_issue_row(child)
        child_issue_rows.append(child_row)
        resolution = resolve_child_epic_stage(
            child_issue=child,
            allowed_stages=allowed_stage_options,
            theme_summary=theme_summary,
            value_stream_name=value_stream_name,
        )
        child_issue_stage_resolution_debug.append(resolution)
        if not resolution.get("included"):
            child_key = resolution.get("child_key") or ""
            child_sources = (child_lookup_debug or {}).get("child_issue_sources") or {}
            unresolved.append(
                _unresolved_stage_mention(
                    resolution=resolution,
                    source="parent_link_child_epic_summary",
                    source_type="child_epic",
                    epic_key=child_key,
                    theme_key=theme_key,
                    value_stream_name=value_stream_name,
                    allowed_stages=allowed_stage_options,
                    extra={
                        "child_key": child_key,
                        "child_lookup_source": list(child_sources.get(child_key) or []),
                    },
                )
            )
            continue

        mention = {
            "raw_stage": resolution.get("cleaned_stage_name") or "",
            "source": "parent_link_child_epic_summary",
            "source_text": resolution.get("raw_summary") or "",
            "child_key": resolution.get("child_key") or "",
            "theme_key": theme_key,
        }
        child_issue_mentions_debug.append(mention)
        canonicalization_debug.append(
            {
                "raw_stage": resolution.get("cleaned_stage_name") or "",
                "source": "parent_link_child_epic_summary",
                "child_key": resolution.get("child_key") or "",
                "theme_key": theme_key,
                "source_text": resolution.get("raw_summary") or "",
                "canonical": resolution.get("canonical_stage"),
                "match_method": resolution.get("match_method"),
                "confidence": resolution.get("confidence"),
                "warnings": list(resolution.get("warnings") or []),
            }
        )
        canonical = resolution.get("canonical_stage")
        if canonical:
            add_verified_stage(
                by_canonical,
                canonical=str(canonical),
                confidence=float(resolution.get("confidence") or 0.0),
                match_method=str(resolution.get("match_method") or ""),
                raw_payload=_raw_mention_payload(mention),
            )

    verified_stages = sorted(by_canonical.values(), key=lambda item: item["canonical"])
    warnings.extend(str(item) for item in (child_lookup_debug or {}).get("warnings") or [])

    return {
        "theme_key": theme_key,
        "theme_summary": theme_summary,
        "business_value_stream": business_value_stream,
        "allowed_stages": allowed_stages,
        "business_needs_raw": business_needs_raw,
        "business_needs_mentions_debug_only": business_needs_mentions_debug_only,
        "child_issue_lookup": child_lookup_debug or {},
        "linked_epic_rows": linked_epics,
        "linked_epic_stage_resolution_debug": linked_epic_stage_resolution_debug,
        "linked_epic_mentions_debug": linked_epic_mentions_debug,
        "child_issue_mentions_debug": child_issue_mentions_debug,
        "child_issue_stage_resolution_debug": child_issue_stage_resolution_debug,
        "canonicalization_debug": canonicalization_debug,
        "verified_stages": verified_stages,
        "unresolved_stage_mentions": unresolved,
        "child_issues": child_issue_rows,
        "warnings": warnings,
    }


def find_linked_theme_issues(issue: dict[str, Any]) -> list[dict[str, Any]]:
    """Deprecated compatibility wrapper around Jira value-stream theme extraction."""
    fields = issue.get("fields") or {}
    return extract_themes(
        list(fields.get("issuelinks") or []),
        source_title=_clean_text(fields.get("summary")),
    )


# Link-type tokens that are usually relationship/dependency links, not
# stage-membership links. Used only to filter the non-implement Epic-link
# expansion; implement links are always kept (high-confidence, unchanged
# behavior). Words like "relate"/"depend" are not always impossible, but they
# are risky enough to exclude in this conservative expansion.
_NON_STAGE_EPIC_LINK_TOKENS = (
    "relate",
    "duplicate",
    "clone",
    "block",
    "depend",
    "caused",
    "split",
    "supersede",
)


def _is_non_stage_epic_link(link_type_text: str) -> bool:
    text = link_type_text.lower()
    return any(token in text for token in _NON_STAGE_EPIC_LINK_TOKENS)


def extract_epic_links_from_theme_issue(theme_issue: dict[str, Any]) -> list[dict[str, Any]]:
    fields = theme_issue.get("fields") or {}
    links = [link for link in (fields.get("issuelinks") or []) if isinstance(link, dict)]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _record(linked: dict[str, Any], *, link_type_text: str, side: str, link_source: str) -> None:
        key = normalize_ticket_key(linked.get("key"))
        if not key or key in seen:
            return
        seen.add(key)
        out.append(
            {
                "key": key,
                "summary": _clean_text((linked.get("fields") or {}).get("summary") or linked.get("summary")),
                "status": _status_name(linked),
                "issue_type": _issue_type_name(linked),
                "link_direction": side,
                "link_type": link_type_text,
                "link_source": link_source,
            }
        )

    # Pass 1: implement links (high-confidence; unchanged selection behavior).
    for link in links:
        link_type_text = _link_type_text(link.get("type") or {})
        if "implement" not in link_type_text.lower():
            continue
        for side in ("outwardIssue", "inwardIssue"):
            linked = link.get(side)
            if isinstance(linked, dict) and _is_stage_epic_link_candidate(linked):
                _record(linked, link_type_text=link_type_text, side=side, link_source="implement")

    # Pass 2: other links to Epics, excluding obvious non-stage relationships.
    # Stricter than pass 1: the linked issue type must be exactly Epic. Runs
    # after pass 1 so implement links keep precedence on dedupe.
    for link in links:
        link_type_text = _link_type_text(link.get("type") or {})
        if "implement" in link_type_text.lower():
            continue
        if _is_non_stage_epic_link(link_type_text):
            continue
        for side in ("outwardIssue", "inwardIssue"):
            linked = link.get(side)
            if isinstance(linked, dict) and _issue_type_name(linked).lower() == "epic":
                _record(linked, link_type_text=link_type_text, side=side, link_source="non_implement_epic")

    return out


def _issue_keys(issues: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for issue in issues:
        key = _clean_text(issue.get("key"))
        if key:
            keys.add(key)
    return keys


async def _run_child_epic_lookup(
    jira_client: Any,
    jql: str,
    *,
    attempt: dict[str, Any],
    warnings: list[str],
    failure_warning: str,
) -> list[dict[str, Any]]:
    """Run one child-Epic JQL and record its outcome on ``attempt``.

    Returns the raw issues found (empty on failure or no results); never raises,
    so one lookup path failing does not stop the other.
    """
    try:
        result = await _search_issues(jira_client, jql, fields=CHILD_EPIC_FIELDS)
    except Exception as exc:
        attempt["error"] = _compact_error(exc)
        warnings.append(failure_warning)
        return []

    issues = [issue for issue in (result.get("issues") or []) if isinstance(issue, dict)]
    keys: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        key = _clean_text(issue.get("key"))
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    attempt["keys"] = keys
    attempt["count"] = len(keys)
    return issues


async def fetch_direct_child_epics(
    *,
    jira_client: Any,
    theme_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_theme_key = _clean_text(theme_key).upper()
    parent_link_jql = f'"Parent Link" = {normalized_theme_key} AND issuetype = Epic'
    parent_jql = f"parent = {normalized_theme_key} AND issuetype = Epic"
    lookup: dict[str, Any] = {
        "theme_key": normalized_theme_key,
        "lookup_strategy": "parent_link_and_parent",
        "jql_attempts": [
            {
                "name": "parent_link",
                "jql": parent_link_jql,
                "count": 0,
                "keys": [],
                "error": None,
            },
            {
                "name": "parent",
                "jql": parent_jql,
                "count": 0,
                "keys": [],
                "error": None,
            },
        ],
        "child_issue_keys": [],
        "child_issue_sources": {},
        "warnings": [],
    }

    # Two independent lookups: the original Parent Link custom field plus the
    # standard Jira ``parent`` relation. Each is guarded so one failing/empty
    # does not affect the other; Parent Link alone reproduces old behavior.
    parent_link_issues = await _run_child_epic_lookup(
        jira_client,
        parent_link_jql,
        attempt=lookup["jql_attempts"][0],
        warnings=lookup["warnings"],
        failure_warning="direct Parent Link child Epic lookup failed",
    )
    parent_issues = await _run_child_epic_lookup(
        jira_client,
        parent_jql,
        attempt=lookup["jql_attempts"][1],
        warnings=lookup["warnings"],
        failure_warning="Jira parent-relation child Epic lookup failed",
    )

    # Parent Link results take precedence; parent-relation adds any extras.
    # _dedupe_child_epics dedupes by key (first seen wins) and drops non-Epics.
    child_epics = _dedupe_child_epics(parent_link_issues + parent_issues, lookup["warnings"])
    child_epics = sorted(child_epics, key=lambda item: _clean_text(item.get("key")))

    parent_link_keys = _issue_keys(parent_link_issues)
    parent_keys = _issue_keys(parent_issues)

    child_keys: list[str] = []
    child_sources: dict[str, list[str]] = {}
    for child in child_epics:
        key = _clean_text(child.get("key"))
        if not key:
            continue
        child_keys.append(key)
        found_in: list[str] = []
        if key in parent_link_keys:
            found_in.append("parent_link")
        if key in parent_keys:
            found_in.append("parent")
        child_sources[key] = found_in

    lookup["child_issue_keys"] = child_keys
    lookup["child_issue_sources"] = child_sources
    if not child_keys:
        lookup["warnings"].append("no direct child Epics found via Parent Link or parent relation")
    return child_epics, lookup


def extract_stage_from_child_epic_summary(
    child_issue: dict[str, Any],
    *,
    theme_summary: str = "",
    value_stream_name: str = "",
) -> dict[str, Any] | None:
    raw_summary, candidates = generate_stage_candidates_from_child_epic_summary(
        child_issue=child_issue,
        theme_summary=theme_summary,
        value_stream_name=value_stream_name,
    )
    if not raw_summary or not candidates:
        return None
    candidate = str(candidates[0].get("candidate") or "").strip()

    return {
        "raw_stage": candidate,
        "source": "child_epic_summary",
        "source_text": raw_summary,
        "child_key": _clean_text(child_issue.get("key")),
    }


def generate_stage_candidates_from_child_epic_summary(
    *,
    child_issue: dict[str, Any],
    theme_summary: str = "",
    value_stream_name: str = "",
) -> tuple[str, list[dict[str, str]]]:
    raw_summary = _clean_text((child_issue.get("fields") or {}).get("summary") or child_issue.get("summary"))
    if not raw_summary:
        return "", []

    suffix = _child_summary_suffix(
        raw_summary,
        theme_summary=theme_summary,
        value_stream_name=value_stream_name,
    )
    suffix_segments = [
        _clean_child_stage_candidate(segment)
        for segment in re.split(r"\s*[-\u2013\u2014]\s*", suffix)
        if _clean_child_stage_candidate(segment)
    ]

    candidates: list[dict[str, str]] = []
    if suffix_segments:
        candidates.append({"candidate": suffix_segments[0], "rule": "first_suffix_segment"})
        for segment in suffix_segments[1:]:
            candidates.append({"candidate": segment, "rule": "later_suffix_segment"})
        joined = _clean_child_stage_candidate(" ".join(suffix_segments))
        if joined:
            candidates.append({"candidate": joined, "rule": "joined_suffix"})

    full_raw = _clean_stage_candidate(raw_summary)
    if full_raw:
        candidates.append({"candidate": full_raw, "rule": "full_raw_summary"})

    return raw_summary, _dedupe_candidate_rows(candidates)


def resolve_child_epic_stage(
    *,
    child_issue: dict[str, Any],
    allowed_stages: list[Any],
    theme_summary: str = "",
    value_stream_name: str = "",
    confidence_threshold: float = 0.86,
) -> dict[str, Any]:
    from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage

    raw_summary, candidates = generate_stage_candidates_from_child_epic_summary(
        child_issue=child_issue,
        theme_summary=theme_summary,
        value_stream_name=value_stream_name,
    )
    child_key = _clean_text(child_issue.get("key"))
    scored_candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    for candidate in candidates:
        candidate_text = str(candidate.get("candidate") or "").strip()
        result = canonicalize_stage(
            candidate_text,
            allowed_stages,
            value_stream_name=value_stream_name,
        )
        scored = {
            "candidate": candidate_text,
            "rule": candidate.get("rule") or "",
            "canonical": result.get("canonical"),
            "match_method": result.get("match_method"),
            "confidence": result.get("confidence"),
            "warnings": list(result.get("warnings") or []),
        }
        scored_candidates.append(scored)
        if (
            selected is None
            and result.get("canonical")
            and float(result.get("confidence") or 0.0) >= confidence_threshold
        ):
            selected = scored

    return {
        "child_key": child_key,
        "raw_summary": raw_summary,
        "candidates": scored_candidates,
        "included": selected is not None,
        "cleaned_stage_name": selected.get("candidate") if selected else "",
        "canonical_stage": selected.get("canonical") if selected else None,
        "match_method": selected.get("match_method") if selected else "unresolved",
        "confidence": selected.get("confidence") if selected else 0.0,
        "selected_candidate_rule": selected.get("rule") if selected else "",
        "warnings": [] if selected else ["no confident approved stage match from child Epic summary candidates"],
    }


def resolve_linked_epic_stage(
    *,
    linked_issue: dict[str, Any],
    allowed_stages: list[Any],
    theme_summary: str = "",
    value_stream_name: str = "",
    confidence_threshold: float = 0.86,
) -> dict[str, Any]:
    from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage

    raw_summary, candidates = generate_stage_candidates_from_child_epic_summary(
        child_issue=linked_issue,
        theme_summary=theme_summary,
        value_stream_name=value_stream_name,
    )
    linked_issue_key = normalize_ticket_key(linked_issue.get("key"))
    scored_candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    for candidate in candidates:
        candidate_text = str(candidate.get("candidate") or "").strip()
        result = canonicalize_stage(
            candidate_text,
            allowed_stages,
            value_stream_name=value_stream_name,
        )
        scored = {
            "candidate": candidate_text,
            "rule": candidate.get("rule") or "",
            "canonical": result.get("canonical"),
            "match_method": result.get("match_method"),
            "confidence": result.get("confidence"),
            "warnings": list(result.get("warnings") or []),
        }
        scored_candidates.append(scored)
        if (
            selected is None
            and result.get("canonical")
            and float(result.get("confidence") or 0.0) >= confidence_threshold
        ):
            selected = scored

    return {
        "linked_issue_key": linked_issue_key,
        "raw_summary": raw_summary,
        "candidates": scored_candidates,
        "included": selected is not None,
        "cleaned_stage_name": selected.get("candidate") if selected else "",
        "canonical_stage": selected.get("canonical") if selected else None,
        "match_method": selected.get("match_method") if selected else "unresolved",
        "confidence": selected.get("confidence") if selected else 0.0,
        "selected_candidate_rule": selected.get("rule") if selected else "",
        "warnings": [] if selected else ["no confident approved stage match from Theme issue-link Epic summary candidates"],
    }


def add_verified_stage(
    by_canonical: dict[str, dict[str, Any]],
    *,
    canonical: str,
    confidence: float,
    match_method: str,
    raw_payload: dict[str, Any],
) -> None:
    canonical_name = _clean_text(canonical)
    if not canonical_name:
        return
    stage = by_canonical.setdefault(
        canonical_name,
        {
            "canonical": canonical_name,
            "confidence": 0.0,
            "match_method": "",
            "raw_mentions": [],
        },
    )
    stage["raw_mentions"].append(raw_payload)
    current_confidence = float(stage.get("confidence") or 0.0)
    if confidence > current_confidence:
        stage["confidence"] = round(max(0.0, min(1.0, confidence)), 4)
        stage["match_method"] = match_method
    elif match_method == "exact" and stage.get("match_method") != "exact":
        stage["match_method"] = "exact"


def extract_raw_stage_mentions_from_child_issue(
    child_issue: dict[str, Any],
    *,
    theme_summary: str = "",
    value_stream_name: str = "",
) -> list[dict[str, Any]]:
    """Deprecated compatibility wrapper for the direct child Epic extractor."""
    mention = extract_stage_from_child_epic_summary(
        child_issue,
        theme_summary=theme_summary,
        value_stream_name=value_stream_name,
    )
    return [mention] if mention else []


def _dedupe_child_epics(issues: list[dict[str, Any]], warnings: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        key = _clean_text(issue.get("key"))
        if not key or key in seen:
            continue
        issue_type = _issue_type_name(issue)
        if issue_type and issue_type.lower() != "epic":
            warnings.append(f"skipped non-Epic child issue from Parent Link lookup: {key} ({issue_type})")
            continue
        if not issue_type:
            warnings.append(f"child issue from Parent Link lookup has no issue type: {key}")
        seen.add(key)
        out.append(issue)
    return out


def _child_summary_suffix(
    raw_summary: str,
    *,
    theme_summary: str = "",
    value_stream_name: str = "",
) -> str:
    summary = _clean_text(raw_summary)
    for prefix in (value_stream_name, theme_summary):
        prefix_text = _clean_text(prefix)
        if not prefix_text:
            continue
        idx = summary.lower().find(prefix_text.lower())
        if idx != -1:
            return _strip_candidate_prefix_separators(summary[idx + len(prefix_text) :])
    if " : " in summary:
        return _strip_candidate_prefix_separators(summary.rsplit(" : ", 1)[-1])
    if ":" in summary:
        return _strip_candidate_prefix_separators(summary.rsplit(":", 1)[-1])
    return summary


def _strip_candidate_prefix_separators(value: str) -> str:
    return re.sub(r"^\s*[:\-\u2013\u2014]+\s*", "", str(value or "").strip())


def _clean_child_stage_candidate(value: Any) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(value or ""))
    return _clean_stage_candidate(text)


def _dedupe_candidate_rows(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate.get("candidate") or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append({"candidate": text, "rule": str(candidate.get("rule") or "")})
    return out


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


def _compact_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


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
    raw_stage = str(mention.get("raw_stage") or "").strip()
    payload = {
        "raw_stage": raw_stage,
        "raw": raw_stage,
        "source": str(mention.get("source") or "").strip(),
    }
    for key in ("source_text", "position", "child_key", "linked_issue_key", "theme_key"):
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


def _is_stage_epic_link_candidate(issue: dict[str, Any]) -> bool:
    issue_type = _issue_type_name(issue).lower()
    if issue_type == "epic":
        return True
    key = normalize_ticket_key(issue.get("key"))
    summary = _clean_text((issue.get("fields") or {}).get("summary") or issue.get("summary"))
    return key.startswith("GROUP-") and _looks_like_stage_child_record_summary(summary)


def _looks_like_stage_child_record_summary(summary: str) -> bool:
    text = _clean_text(summary)
    if not text:
        return False
    if re.search(r"\s[-\u2013\u2014]\s", text):
        return True
    return bool(re.search(r"\b(stage|value stage)\b", text, re.I))


def _link_type_text(link_type: dict[str, Any]) -> str:
    return " | ".join(
        _clean_text(link_type.get(key))
        for key in ("name", "outward", "inward")
        if _clean_text(link_type.get(key))
    )


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
    "CHILD_EPIC_FIELDS",
    "IDMT_FIELDS",
    "PARENT_LINK_FIELD",
    "THEME_FIELDS",
    "add_verified_stage",
    "business_value_stream_from_resolved_link",
    "build_theme_stage_ground_truth",
    "build_ticket_stage_ground_truth",
    "extract_epic_links_from_theme_issue",
    "extract_stage_from_child_epic_summary",
    "extract_raw_stage_mentions_from_business_needs",
    "fetch_direct_child_epics",
    "find_linked_theme_issues",
    "generate_stage_candidates_from_child_epic_summary",
    "normalize_ticket_key",
    "parse_business_value_stream",
    "resolve_child_epic_stage",
    "resolve_linked_epic_stage",
]
