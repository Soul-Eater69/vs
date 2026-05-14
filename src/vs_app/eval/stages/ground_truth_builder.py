from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from vs_app.eval.stages.stage_ground_truth_models import (
    StageGroundTruthStage,
    StageGroundTruthTicket,
    StageGroundTruthValueStream,
)
from vs_app.ingestion.jira.mapper import build_ticket_payload
from vs_app.ingestion.jira.value_stream_labels.theme_extraction import extract_themes
from vs_app.ingestion.jira.value_stream_labels.approved_registry import approved_value_stream_id
from vs_app.modules.value_streams.canonical import canonicalize_value_stream_name


logger = logging.getLogger(__name__)

_JIRA_FIELDS = [
    "summary",
    "status",
    "resolution",
    "issuetype",
    "issuelinks",
    "parent",
]

StageResolver = Callable[..., str | None]
ValueStreamIdResolver = Callable[[str], str | None]


async def build_stage_ground_truth_for_tickets(
    *,
    ticket_keys: list[str],
    jira_client: Any,
    include_cancelled_epics: bool = True,
    value_stream_id_resolver: ValueStreamIdResolver | None = None,
    stage_id_resolver: StageResolver | None = None,
    stage_index_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    keys = [normalize_ticket_key(key) for key in ticket_keys if normalize_ticket_key(key)]
    if not keys:
        return out

    for ticket_key in keys:
        rows = await fetch_theme_epic_rows(jira_client=jira_client, ticket_key=ticket_key)
        out.update(
            rows_to_ticket_ground_truth(
                rows,
                include_cancelled_epics=include_cancelled_epics,
                value_stream_id_resolver=value_stream_id_resolver,
                stage_id_resolver=stage_id_resolver,
                stage_index_name=stage_index_name,
            )
        )
    return out


async def fetch_theme_epic_rows(
    *,
    jira_client: Any,
    ticket_key: str,
) -> list[dict[str, Any]]:
    ticket_payload = await _fetch_ticket_payload(jira_client, normalize_ticket_key(ticket_key))
    ticket_id = normalize_ticket_key(str(ticket_payload.get("key") or ticket_key))
    fields = ticket_payload.get("fields") or {}
    ticket_summary = clean_text(fields.get("summary"))
    themes = list(ticket_payload.get("themes") or [])
    if not themes:
        themes = extract_themes(fields.get("issuelinks") or [], source_title=ticket_summary)

    rows: list[dict[str, Any]] = []
    for theme in themes:
        theme_key = clean_text(theme.get("key"))
        if not theme_key:
            continue

        theme_issue = await _fetch_issue(jira_client, theme_key)
        theme_fields = theme_issue.get("fields") or {}
        theme_summary = clean_text(
            theme_fields.get("summary")
            or theme.get("summary_raw")
            or theme.get("summary")
        )
        theme_issue_type = _issue_type_name(theme_issue) or clean_text(theme.get("issue_type"))
        theme_business_value_streams = (
            theme_issue.get("businessValueStreams")
            or theme_fields.get("businessValueStreams")
            or theme.get("raw_value_stream_suffix")
            or None
        )

        stage_issues = await _fetch_stage_epics_for_theme(
            jira_client=jira_client,
            theme_key=theme_key,
            theme_summary=theme_summary,
            theme_issue=theme_issue,
        )
        if not stage_issues:
            rows.append(
                _stage_row(
                    ticket_id=ticket_id,
                    ticket_summary=ticket_summary,
                    theme_key=theme_key,
                    theme_summary=theme_summary,
                    theme_issue_type=theme_issue_type,
                    theme_business_value_streams=theme_business_value_streams,
                    stage_issue=None,
                )
            )
            continue

        for stage_issue in stage_issues:
            rows.append(
                _stage_row(
                    ticket_id=ticket_id,
                    ticket_summary=ticket_summary,
                    theme_key=theme_key,
                    theme_summary=theme_summary,
                    theme_issue_type=theme_issue_type,
                    theme_business_value_streams=theme_business_value_streams,
                    stage_issue=stage_issue,
                )
            )

    return rows


async def _fetch_ticket_payload(jira_client: Any, ticket_key: str) -> dict[str, Any]:
    get_ticket_data = getattr(jira_client, "get_ticket_data", None)
    if callable(get_ticket_data):
        return dict(await get_ticket_data(ticket_key))

    issue = await _fetch_issue(jira_client, ticket_key)
    return build_ticket_payload(issue, ticket_id=ticket_key)


async def _fetch_stage_epics_for_theme(
    *,
    jira_client: Any,
    theme_key: str,
    theme_summary: str,
    theme_issue: dict[str, Any],
) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}

    for issue in _extract_linked_epics(theme_issue):
        hydrated = await _hydrate_issue(jira_client, issue)
        key = clean_text(hydrated.get("key"))
        if key:
            by_key[key] = hydrated

    for issue in await _search_stage_epics_by_theme(jira_client, theme_key, theme_summary):
        key = clean_text(issue.get("key"))
        if key and key not in by_key:
            by_key[key] = issue

    return sorted(by_key.values(), key=lambda issue: clean_text(issue.get("key")))


def _extract_linked_epics(issue: dict[str, Any]) -> list[dict[str, Any]]:
    fields = issue.get("fields") or {}
    out: list[dict[str, Any]] = []
    for link in fields.get("issuelinks") or []:
        for side in ("outwardIssue", "inwardIssue"):
            linked = link.get(side)
            if isinstance(linked, dict) and _issue_type_name(linked).lower() == "epic":
                out.append(linked)
    return out


async def _search_stage_epics_by_theme(
    jira_client: Any,
    theme_key: str,
    theme_summary: str,
) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for jql in _stage_epic_jql_candidates(theme_key, theme_summary):
        try:
            result = await _search_issues(jira_client, jql, fields=_JIRA_FIELDS)
        except Exception as exc:
            logger.info("Stage Epic JQL failed for %s: %s", theme_key, exc)
            continue

        for issue in result.get("issues") or []:
            if _issue_type_name(issue).lower() != "epic":
                continue
            if not _stage_belongs_to_theme(theme_summary, _issue_summary(issue)):
                continue
            key = clean_text(issue.get("key"))
            if key:
                out[key] = issue

    return sorted(out.values(), key=lambda issue: clean_text(issue.get("key")))


def _stage_epic_jql_candidates(theme_key: str, theme_summary: str) -> list[str]:
    project_key = clean_text(theme_key).split("-", 1)[0] or "GROUP"
    summary_probe = _jql_summary_probe(theme_summary)
    candidates = [
        f'"Parent Link" = {theme_key}',
        f"parent = {theme_key}",
    ]
    if summary_probe:
        candidates.append(
            f'project = {project_key} AND issuetype = Epic AND summary ~ "{_jql_quote(summary_probe)}"'
        )
    return candidates


def _jql_summary_probe(theme_summary: str) -> str:
    summary = clean_text(theme_summary)
    if " : " in summary:
        return summary.rsplit(" : ", 1)[-1].strip()
    if " - " in summary:
        return summary.rsplit(" - ", 1)[-1].strip()
    return summary


def _jql_quote(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _stage_belongs_to_theme(theme_summary: str, stage_summary: str) -> bool:
    theme = clean_text(theme_summary).lower()
    stage = clean_text(stage_summary).lower()
    return bool(theme and stage.startswith(f"{theme} - "))


async def _hydrate_issue(jira_client: Any, issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields") or {}
    if fields.get("summary") and _issue_type_name(issue):
        return issue
    key = clean_text(issue.get("key"))
    return await _fetch_issue(jira_client, key) if key else issue


async def _fetch_issue(jira_client: Any, issue_key: str) -> dict[str, Any]:
    get_issue = getattr(jira_client, "get_issue_by_key", None)
    if callable(get_issue):
        return dict(await get_issue(issue_key, fields=_JIRA_FIELDS))

    nested_client = getattr(jira_client, "client", None)
    nested_get_issue = getattr(nested_client, "get_issue_by_key", None)
    if callable(nested_get_issue):
        return dict(await nested_get_issue(issue_key, fields=_JIRA_FIELDS))

    raise TypeError("jira_client must provide get_issue_by_key or get_ticket_data")


async def _search_issues(
    jira_client: Any,
    jql: str,
    *,
    fields: list[str],
) -> dict[str, Any]:
    get_issues = getattr(jira_client, "get_issues", None)
    if callable(get_issues):
        return dict(await get_issues(jql=jql, start_at=0, max_results=100, fields=fields))

    nested_client = getattr(jira_client, "client", None)
    nested_get_issues = getattr(nested_client, "get_issues", None)
    if callable(nested_get_issues):
        return dict(
            await nested_get_issues(jql=jql, start_at=0, max_results=100, fields=fields)
        )

    search_issues = getattr(jira_client, "search_issues", None)
    if callable(search_issues):
        return dict(await search_issues(jql, start_at=0, max_results=100))

    return {"issues": [], "total": 0}


def _stage_row(
    *,
    ticket_id: str,
    ticket_summary: str,
    theme_key: str,
    theme_summary: str,
    theme_issue_type: str,
    theme_business_value_streams: Any,
    stage_issue: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "ticket_summary": ticket_summary,
        "theme_key": theme_key,
        "theme_summary": theme_summary,
        "theme_issue_type": theme_issue_type,
        "theme_business_value_streams": theme_business_value_streams,
        "stage_issue_key": clean_text((stage_issue or {}).get("key")),
        "stage_summary": _issue_summary(stage_issue or {}),
        "stage_issue_type": _issue_type_name(stage_issue or {}),
        "stage_status": _issue_status_name(stage_issue or {}),
        "stage_resolution": _issue_resolution_name(stage_issue or {}),
    }


def rows_to_ticket_ground_truth(
    rows: list[dict[str, Any]],
    *,
    include_cancelled_epics: bool = True,
    value_stream_id_resolver: ValueStreamIdResolver | None = None,
    stage_id_resolver: StageResolver | None = None,
    stage_index_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not rows:
        return {}

    first = rows[0]
    ticket_id = normalize_ticket_key(str(first.get("ticket_id") or ""))
    ticket_summary = clean_text(first.get("ticket_summary"))
    if not ticket_id:
        return {}

    resolve_vs_id = value_stream_id_resolver or resolve_value_stream_id
    resolve_stage = stage_id_resolver or resolve_stage_id
    by_theme: dict[str, StageGroundTruthValueStream] = {}
    stage_keys_by_theme: dict[str, set[str]] = {}

    for row in rows:
        if not is_valid_theme_value_stream(row):
            continue

        theme_key = clean_text(row.get("theme_key"))
        if not theme_key:
            continue

        theme_summary = clean_text(row.get("theme_summary"))
        vs_info = parse_value_stream_from_theme(
            ticket_summary=ticket_summary,
            theme_summary=theme_summary,
            theme_business_value_streams=row.get("theme_business_value_streams"),
            value_stream_id_resolver=resolve_vs_id,
        )
        if not vs_info:
            continue

        theme_entry = by_theme.setdefault(
            theme_key,
            StageGroundTruthValueStream(
                theme_issue_key=theme_key,
                theme_summary=theme_summary,
                value_stream_name_raw=str(vs_info["raw"]),
                value_stream_name_canonical=str(vs_info["canonical"]),
                value_stream_id=vs_info.get("value_stream_id"),
                stage_scope="specific_stages",
            ),
        )
        stage_keys = stage_keys_by_theme.setdefault(theme_key, set())

        stage_issue_key = clean_text(row.get("stage_issue_key"))
        stage_summary = clean_text(row.get("stage_summary"))
        if not stage_issue_key or not stage_summary:
            continue
        if not include_cancelled_epics and is_cancelled_stage(row):
            continue
        if stage_issue_key in stage_keys:
            continue

        stage_name = parse_stage_from_epic(
            theme_summary=theme_summary,
            stage_summary=stage_summary,
        )
        if not stage_name:
            continue

        theme_entry.stages.append(
            StageGroundTruthStage(
                stage_issue_key=stage_issue_key,
                stage_summary=stage_summary,
                stage_name_raw=stage_name,
                stage_name_canonical=stage_name,
                stage_id=resolve_stage(
                    value_stream_id=theme_entry.value_stream_id,
                    value_stream_name=theme_entry.value_stream_name_canonical,
                    stage_name=stage_name,
                    index_name=stage_index_name,
                ),
                status=_optional_clean_text(row.get("stage_status")),
                resolution=_optional_clean_text(row.get("stage_resolution")),
            )
        )
        stage_keys.add(stage_issue_key)

    value_streams = sorted(by_theme.values(), key=lambda item: item.theme_issue_key)
    for value_stream in value_streams:
        if not value_stream.stages:
            value_stream.stage_scope = "broad_or_unclear"
        value_stream.stages.sort(key=lambda stage: stage.stage_issue_key)

    if not value_streams:
        return {}

    ticket = StageGroundTruthTicket(
        ticket_id=ticket_id,
        ticket_summary=ticket_summary,
        value_streams=value_streams,
    )
    return {ticket_id: ticket.to_dict()}


def parse_value_stream_from_theme(
    *,
    ticket_summary: str,
    theme_summary: str,
    theme_business_value_streams: Any,
    value_stream_id_resolver: ValueStreamIdResolver | None = None,
) -> dict[str, str | None] | None:
    raw_candidates: list[str] = []
    raw_candidates.extend(_listify_text_values(theme_business_value_streams))

    summary = clean_text(theme_summary)
    if " : " in summary:
        raw_candidates.append(summary.rsplit(" : ", 1)[-1].strip())
    if " - " in summary:
        raw_candidates.append(summary.rsplit(" - ", 1)[-1].strip())
    if summary:
        raw_candidates.append(summary)

    seen: set[str] = set()
    resolve_vs_id = value_stream_id_resolver or resolve_value_stream_id
    for raw in raw_candidates:
        raw = clean_text(raw)
        key = raw.lower()
        if not raw or key in seen:
            continue
        seen.add(key)
        canonical = canonicalize_value_stream_name(raw)
        if canonical:
            return {
                "raw": raw,
                "canonical": canonical,
                "value_stream_id": resolve_vs_id(canonical),
            }

    return None


def parse_stage_from_epic(
    *,
    theme_summary: str,
    stage_summary: str,
) -> str:
    theme = clean_text(theme_summary)
    stage = clean_text(stage_summary)

    prefix = f"{theme} - "
    if theme and stage.lower().startswith(prefix.lower()):
        return stage[len(prefix) :].strip()

    if " - " in stage:
        return stage.rsplit(" - ", 1)[-1].strip()

    return stage


def resolve_value_stream_id(value_stream_name: str) -> str | None:
    value = approved_value_stream_id(value_stream_name)
    return value or None


def resolve_stage_id(
    *,
    value_stream_id: str | None,
    value_stream_name: str,
    stage_name: str,
    index_name: str | None = None,
) -> str | None:
    from vs_app.modules.stages.catalog import get_stages_for_value_stream

    stages = get_stages_for_value_stream(
        value_stream_id=value_stream_id or "",
        value_stream_name=value_stream_name,
        index_name=index_name
        or os.environ.get(
            "VALUE_STREAM_AZURE_SEARCH_INDEX_NAME",
            os.environ.get("AZURE_SEARCH_INDEX_NAME", "value-streams"),
        ),
    )

    target = normalize_stage_name(stage_name)
    for stage in stages:
        if normalize_stage_name(stage.get("stage_name")) == target:
            return clean_text(stage.get("stage_id")) or None

    return None


def is_valid_theme_value_stream(row: dict[str, Any]) -> bool:
    if clean_text(row.get("theme_issue_type")).lower() != "theme":
        return False
    return bool(
        parse_value_stream_from_theme(
            ticket_summary=clean_text(row.get("ticket_summary")),
            theme_summary=clean_text(row.get("theme_summary")),
            theme_business_value_streams=row.get("theme_business_value_streams"),
        )
    )


def is_cancelled_stage(row: dict[str, Any]) -> bool:
    return clean_text(row.get("stage_status")).lower() == "cancelled"


def _issue_summary(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    return clean_text(fields.get("summary") or issue.get("summary"))


def _issue_type_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    issue_type = fields.get("issuetype") or issue.get("issueType")
    if isinstance(issue_type, dict):
        return clean_text(issue_type.get("name"))
    return clean_text(issue_type)


def _issue_status_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    status = fields.get("status") or issue.get("status")
    if isinstance(status, dict):
        return clean_text(status.get("name"))
    return clean_text(status)


def _issue_resolution_name(issue: dict[str, Any]) -> str:
    fields = issue.get("fields") or {}
    resolution = fields.get("resolution") or issue.get("resolution")
    if isinstance(resolution, dict):
        return clean_text(resolution.get("name"))
    return clean_text(resolution)


def normalize_stage_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_ticket_key(value: str) -> str:
    return clean_text(value).upper()


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _optional_clean_text(value: Any) -> str | None:
    text = clean_text(value)
    return text or None


def _listify_text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        text = clean_text(value)
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, list):
                return [clean_text(item) for item in loaded if clean_text(item)]
        return [part.strip() for part in text.split(";") if part.strip()]
    return [clean_text(value)] if clean_text(value) else []


__all__ = [
    "build_stage_ground_truth_for_tickets",
    "fetch_theme_epic_rows",
    "is_valid_theme_value_stream",
    "normalize_stage_name",
    "parse_stage_from_epic",
    "parse_value_stream_from_theme",
    "resolve_stage_id",
    "resolve_value_stream_id",
    "rows_to_ticket_ground_truth",
]
