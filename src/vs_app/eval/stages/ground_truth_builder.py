from __future__ import annotations

import json
import os
from typing import Any, Callable

from vs_app.eval.stages.stage_ground_truth_models import (
    StageGroundTruthStage,
    StageGroundTruthTicket,
    StageGroundTruthValueStream,
)
from vs_app.ingestion.jira.value_stream_labels.approved_registry import approved_value_stream_id
from vs_app.modules.value_streams.canonical import canonicalize_value_stream_name


THEME_EPIC_QUERY = """
MATCH (t:JIRA {key: $ticket_key})
WITH t, coalesce(t.inwardIssues, []) AS theme_refs
UNWIND theme_refs AS raw_theme_ref
WITH t, raw_theme_ref,
     CASE
       WHEN raw_theme_ref IS NULL THEN NULL
       WHEN raw_theme_ref CONTAINS " " THEN split(raw_theme_ref, " ")[0]
       ELSE raw_theme_ref
     END AS theme_key
WHERE theme_key STARTS WITH "GROUP-"
MATCH (theme:JIRA {key: theme_key})
WHERE theme.issueType = "Theme"

WITH t, theme,
     coalesce(theme.outwardIssues, []) + coalesce(theme.inwardIssues, []) AS child_refs
UNWIND CASE WHEN size(child_refs) = 0 THEN [NULL] ELSE child_refs END AS raw_child_ref

WITH t, theme, raw_child_ref,
     CASE
       WHEN raw_child_ref IS NULL THEN NULL
       WHEN raw_child_ref CONTAINS " " THEN split(raw_child_ref, " ")[0]
       ELSE raw_child_ref
     END AS child_key

OPTIONAL MATCH (child:JIRA {key: child_key})
WHERE child IS NULL OR child.issueType = "Epic"

RETURN
  t.key AS ticket_id,
  t.summary AS ticket_summary,

  theme.key AS theme_key,
  theme.summary AS theme_summary,
  theme.issueType AS theme_issue_type,
  theme.businessValueStreams AS theme_business_value_streams,

  child.key AS stage_issue_key,
  child.summary AS stage_summary,
  child.issueType AS stage_issue_type,
  child.status AS stage_status,
  child.resolution AS stage_resolution
ORDER BY t.key, theme.key, child.key
"""

StageResolver = Callable[..., str | None]
ValueStreamIdResolver = Callable[[str], str | None]


def build_stage_ground_truth_for_tickets(
    *,
    ticket_keys: list[str],
    neo4j_driver: Any,
    include_cancelled_epics: bool = True,
    value_stream_id_resolver: ValueStreamIdResolver | None = None,
    stage_id_resolver: StageResolver | None = None,
    stage_index_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    keys = [normalize_ticket_key(key) for key in ticket_keys if normalize_ticket_key(key)]
    if not keys:
        return out

    with neo4j_driver.session() as session:
        for ticket_key in keys:
            rows = fetch_theme_epic_rows(neo4j_session=session, ticket_key=ticket_key)
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


def fetch_theme_epic_rows(
    *,
    neo4j_session: Any,
    ticket_key: str,
) -> list[dict[str, Any]]:
    result = neo4j_session.run(THEME_EPIC_QUERY, ticket_key=normalize_ticket_key(ticket_key))
    return [_record_to_dict(row) for row in result]


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


def _record_to_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    data = getattr(record, "data", None)
    if callable(data):
        return dict(data())
    return dict(record)


__all__ = [
    "THEME_EPIC_QUERY",
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

