"""Reusable Jira source extraction.

Builds an :class:`ExtractedIDMTRecord` for one IDMT ticket from an injected,
duck-typed client. The extractor never constructs a live client and never calls
the network directly — that is the caller's responsibility — so it is fully
testable with fakes.

Expected (optional) client methods, called only when present:
- ``get_issue(ticket_id) -> dict``
- ``get_linked_issues(ticket_id) -> list[dict]``       (linked Theme/GROUP issues)
- ``get_attachments(ticket_id) -> list[dict]``
- ``get_attachment_text(attachment) -> str``
- ``get_child_epics(group_id) -> list[dict]``

Lenient: missing methods or missing fields degrade to empty values plus a note in
``source_metadata["warnings"]``; nothing raises.
"""

from __future__ import annotations

from typing import Any

from vs_app.sources.jira.attachments import (
    combine_attachment_texts,
    normalize_attachment_metadata,
)
from vs_app.sources.jira.idea_card import (
    select_idea_card_attachment,
    select_processing_attachments,
)
from vs_app.sources.jira.models import (
    ExtractedEpicRecord,
    ExtractedIDMTRecord,
    ExtractedThemeRecord,
)


def extract_idmt_record(
    *,
    ticket_id: str,
    client: Any,
    include_attachments: bool = True,
    include_themes: bool = True,
    include_epics: bool = True,
) -> ExtractedIDMTRecord:
    """Extract one IDMT record from an injected client. Never raises."""
    warnings: list[str] = []

    issue = _call(client, "get_issue", ticket_id, warnings=warnings) or {}
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else issue
    fields = fields if isinstance(fields, dict) else {}

    title = _text(fields.get("summary") or issue.get("summary"))
    # Jira "summary" is the ticket title; a generated summary is produced later in
    # ingestion. From raw extraction, summary defaults to the title.
    summary = _text(fields.get("summary_text") or fields.get("summary") or issue.get("summary"))
    description = _text(fields.get("description") or issue.get("description"))
    idea_card_text = _text(fields.get("idea_card_text") or issue.get("idea_card_text"))

    fallback_limit = 4
    idea_card_selected = False
    attachment_text = ""
    attachments_meta: list[dict] = []
    if include_attachments:
        attachments = _call(client, "get_attachments", ticket_id, warnings=warnings) or []
        attachments_meta = [normalize_attachment_metadata(a) for a in attachments]

        # Idea-card-first: when an idea-card attachment exists it is the sole
        # authoritative input; otherwise fall back to the top-N attachments. Text
        # is fetched only for the selected attachments.
        process_list, select_warnings = select_processing_attachments(
            attachments, fallback_limit=fallback_limit
        )
        warnings.extend(select_warnings)
        idea_card_selected = select_idea_card_attachment(attachments) is not None

        texts: list[str] = []
        for attachment in process_list:
            text = _call(client, "get_attachment_text", attachment, warnings=warnings)
            if text:
                texts.append(str(text))
        attachment_text = combine_attachment_texts(texts)
        if not attachments:
            warnings.append("no attachments found")

        if idea_card_selected:
            # The idea-card attachment text is the authoritative idea card.
            idea_card_text = attachment_text

    if idea_card_selected:
        extracted_text = idea_card_text
    else:
        extracted_text = combine_attachment_texts([idea_card_text, attachment_text])

    themes: list[ExtractedThemeRecord] = []
    if include_themes:
        linked = _call(client, "get_linked_issues", ticket_id, warnings=warnings) or []
        for linked_issue in linked:
            themes.append(_theme_from_linked(linked_issue, client, include_epics, warnings))
        if not linked:
            warnings.append("no linked themes found")

    source_metadata = {
        "ticket_id": ticket_id,
        "attachments": attachments_meta,
        "idea_card_selected": idea_card_selected,
        "fallback_limit": fallback_limit,
        "theme_count": len(themes),
        "warnings": warnings,
    }
    return ExtractedIDMTRecord(
        ticket_id=ticket_id,
        title=title,
        summary=summary,
        description=description,
        idea_card_text=idea_card_text,
        attachment_text=attachment_text,
        extracted_text=extracted_text,
        themes=themes,
        source_metadata=source_metadata,
        raw=issue if isinstance(issue, dict) else {},
    )


def _theme_from_linked(
    linked_issue: Any,
    client: Any,
    include_epics: bool,
    warnings: list[str],
) -> ExtractedThemeRecord:
    row = linked_issue if isinstance(linked_issue, dict) else {}
    group_id = _text(row.get("group_id") or row.get("key") or row.get("id"))

    epics: list[ExtractedEpicRecord] = []
    if include_epics and group_id:
        children = _call(client, "get_child_epics", group_id, warnings=warnings) or []
        epics = [_epic_from(child) for child in children]

    return ExtractedThemeRecord(
        group_id=group_id,
        theme_summary=_text(row.get("theme_summary") or row.get("summary")),
        business_needs=_text(row.get("business_needs")),
        value_stream_id=_text(row.get("value_stream_id")),
        value_stream_name=_text(row.get("value_stream_name")),
        epics=epics,
        raw=row,
    )


def _epic_from(child: Any) -> ExtractedEpicRecord:
    row = child if isinstance(child, dict) else {}
    return ExtractedEpicRecord(
        epic_id=_text(row.get("epic_id") or row.get("key") or row.get("id")),
        summary=_text(row.get("summary")),
        status=_text(row.get("status")),
        source=_text(row.get("source")),
        raw=row,
    )


def _call(client: Any, method: str, *args: Any, warnings: list[str]) -> Any:
    """Call ``client.method(*args)`` when present; record a warning otherwise."""
    fn = getattr(client, method, None)
    if not callable(fn):
        warnings.append(f"client has no {method}()")
        return None
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - extraction must stay lenient
        warnings.append(f"{method} failed: {type(exc).__name__}")
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = ["extract_idmt_record"]
