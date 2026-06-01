"""Map offline historic ground-truth into Theme-generation POC documents.

Pure mapping only: no Jira, Azure, or LLM calls here. It turns the output of
``build_ticket_stage_ground_truth`` (plus optional value-stream / stage support
rows) into the Feature 9 builder inputs and calls
``build_theme_generation_documents``.

POC field decisions (see Feature 11):
- ``theme_description`` is always ``""`` (no reliable Jira source; the theme
  generation model produces it later).
- value-stream ``evidence`` is always ``""`` (the VS classifier returns a reason,
  not source evidence).
- stage ``stage_id`` is ``""`` unless a support row already carries one (no stage
  catalog resolution here).
Support ``support_type`` is normalized to ``direct``/``implied`` by the builder;
anything else (``weak_broad``/``not_in_context``/``unknown``) becomes ``""``.
"""

from __future__ import annotations

from typing import Any, Iterator

from vs_app.ingestion.index_documents.theme_generation_document_builder import (
    ThemeGenerationStage,
    ThemeGenerationThemeInput,
    ThemeGenerationValueStream,
    build_theme_generation_documents,
)


def theme_generation_documents_from_ground_truth(
    *,
    ticket_id: str,
    idmt_summary: str,
    idmt_description: str,
    linked_themes: list[dict[str, Any]],
    key_terms: list[str] | None = None,
    stakeholders: list[str] | None = None,
    systems_and_products: list[str] | None = None,
    value_stream_support: list[Any] | None = None,
    stage_support: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the IDMT + theme documents for one ticket from its GT + support."""
    vs_support = list(value_stream_support or [])
    stage_support_rows = list(stage_support or [])

    value_streams: list[ThemeGenerationValueStream] = []
    themes: list[ThemeGenerationThemeInput] = []

    for theme in linked_themes or []:
        group_id = _text(theme.get("theme_key"))
        bvs = theme.get("business_value_stream") or {}
        vs_name = _text(bvs.get("name"))
        vs_id = _text(bvs.get("id"))

        vs_match = _match_value_stream_support(vs_support, group_id=group_id, vs_name=vs_name)
        value_streams.append(
            ThemeGenerationValueStream(
                group_id=group_id,
                value_stream_id=vs_id,
                value_stream_name=vs_name,
                support_type=_text(_get(vs_match, "inference_type", "support_type")),
                reason=_text(_get(vs_match, "reason")),
                evidence="",  # POC: VS classifier has no source evidence
            )
        )

        stages: list[ThemeGenerationStage] = []
        for verified in theme.get("verified_stages") or []:
            stage_name = _text(verified.get("canonical"))
            if not stage_name:
                continue
            support = _match_stage_support(
                stage_support_rows, vs_name=vs_name, stage_name=stage_name
            )
            stages.append(
                ThemeGenerationStage(
                    epic_id=_epic_id_from_mentions(verified),
                    stage_id=_text(_get(support, "stage_id")),
                    stage_name=stage_name,
                    support_type=_text(_get(support, "support_type")),
                    reason=_text(_get(support, "reason")),
                    evidence=_text(_get(support, "evidence")),
                )
            )

        themes.append(
            ThemeGenerationThemeInput(
                group_id=group_id,
                theme_description="",  # POC: generated later by the theme model
                business_needs=_text(theme.get("business_needs_raw")),
                stages=stages,
            )
        )

    return build_theme_generation_documents(
        ticket_id=ticket_id,
        summary_text=idmt_summary,
        idmt_description=idmt_description,
        key_terms=list(key_terms or []),
        stakeholders=list(stakeholders or []),
        systems_and_products=list(systems_and_products or []),
        value_streams=value_streams,
        themes=themes,
    )


def theme_generation_documents_from_gt_record(
    ticket_id: str,
    record: dict[str, Any],
    *,
    value_stream_support: list[Any] | None = None,
    stage_support: list[Any] | None = None,
    key_terms: list[str] | None = None,
    stakeholders: list[str] | None = None,
    systems_and_products: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Map a single ``build_ticket_stage_ground_truth`` record into documents."""
    return theme_generation_documents_from_ground_truth(
        ticket_id=ticket_id or _text(record.get("idmt_key")),
        idmt_summary=_text(record.get("idmt_summary")),
        idmt_description=_text(record.get("idmt_description")),
        linked_themes=record.get("linked_themes") or [],
        key_terms=key_terms,
        stakeholders=stakeholders,
        systems_and_products=systems_and_products,
        value_stream_support=value_stream_support,
        stage_support=stage_support,
    )


def theme_generation_documents_from_gt_payload(
    payload: Any,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Map a full offline GT payload (build_stage_ground_truth output) into docs."""
    docs: list[dict[str, Any]] = []
    for index, (ticket_id, record) in enumerate(_iter_gt_records(payload)):
        if limit is not None and index >= limit:
            break
        if not ticket_id or not isinstance(record, dict):
            continue
        docs.extend(theme_generation_documents_from_gt_record(ticket_id, record))
    return docs


def _iter_gt_records(payload: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(payload, dict):
        tickets = payload.get("tickets")
        if isinstance(tickets, dict):
            for key, record in tickets.items():
                if isinstance(record, dict):
                    yield (_text(record.get("idmt_key") or key), record)
            return
        for list_key in ("results", "rows"):
            sequence = payload.get(list_key)
            if isinstance(sequence, list):
                for record in sequence:
                    if isinstance(record, dict):
                        yield (_text(record.get("idmt_key") or record.get("ticket_id")), record)
                return
    if isinstance(payload, list):
        for record in payload:
            if isinstance(record, dict):
                yield (_text(record.get("idmt_key") or record.get("ticket_id")), record)


def _match_value_stream_support(
    rows: list[Any], *, group_id: str, vs_name: str
) -> Any | None:
    group_key = _norm(group_id)
    name_key = _norm(vs_name)
    for row in rows:
        if group_key and _norm(_get(row, "jira_group_id", "group_id")) == group_key:
            return row
    for row in rows:
        if name_key and _norm(_get(row, "vs_name", "value_stream_name")) == name_key:
            return row
    return None


def _match_stage_support(rows: list[Any], *, vs_name: str, stage_name: str) -> Any | None:
    name_key = _norm(vs_name)
    stage_key = _norm(stage_name)
    for row in rows:
        if (
            _norm(_get(row, "value_stream_name", "vs_name")) == name_key
            and _norm(_get(row, "stage_name")) == stage_key
        ):
            return row
    return None


def _epic_id_from_mentions(verified_stage: dict[str, Any]) -> str:
    for mention in verified_stage.get("raw_mentions") or []:
        if not isinstance(mention, dict):
            continue
        for key in ("child_key", "linked_issue_key"):
            value = _text(mention.get(key))
            if value:
                return value
    return ""


def _get(row: Any, *keys: str) -> Any:
    if row is None:
        return ""
    for key in keys:
        if isinstance(row, dict):
            if row.get(key) is not None:
                return row[key]
        else:
            value = getattr(row, key, None)
            if value is not None:
                return value
    return ""


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return _text(value).lower()


__all__ = [
    "theme_generation_documents_from_ground_truth",
    "theme_generation_documents_from_gt_record",
    "theme_generation_documents_from_gt_payload",
]
