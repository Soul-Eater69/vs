from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from vs_app.container import build_ticket_fetcher, normalize_ticket_source
from vs_app.modules.tickets.text_assembly import build_retrieval_text
from vs_app.modules.tickets.text_processing import clean_description, extract_comment_texts

from .corpus_models import RawTicket

logger = logging.getLogger(__name__)
_EXTRACTION_SOURCE_BY_TICKET_SOURCE = {
    "jira": "jira_direct",
}


def _normalize_ticket_ids(ticket_ids: Iterable[object]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for ticket_id in ticket_ids:
        normalized_id = str(ticket_id).strip().upper()
        if not normalized_id:
            continue
        key = normalized_id.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_id)
    return normalized


def _dedupe_labels(values: Any) -> List[str]:
    labels: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = str(value).strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _project_raw_ticket(
    ticket_id: str,
    ticket_data: Dict[str, Any],
    attachment_texts: Optional[List[str]] = None,
    extraction_source: str = "jira_direct",
) -> RawTicket:
    fields = ticket_data.get("fields", {}) or {}
    description = clean_description(fields.get("description"))
    title = str(fields.get("summary") or ticket_id)
    vs_labels = _dedupe_labels(ticket_data.get("value_stream_names"))
    comment_texts = extract_comment_texts(fields.get("comment") or {})
    raw_text = build_retrieval_text(
        title=title,
        description_cleaned=description,
        attachment_texts=attachment_texts,
        comment_texts=comment_texts,
    )

    return RawTicket(
        ticket_id=ticket_id,
        title=title,
        raw_text=raw_text,
        description=description,
        value_stream_labels=vs_labels,
        extraction_source=extraction_source,
        char_count=len(raw_text),
    )


async def _extract_ticket(
    ticket_id: str,
    ticket_client: Any,
    config: Any = None,
    extract_attachments: bool = True,
    extraction_source: str = "jira_direct",
) -> Optional[RawTicket]:
    try:
        ticket_data = await ticket_client.get_ticket_data(
            ticket_id,
            config=config,
        )

        attachment_texts: List[str] = []
        if extract_attachments:
            attachments = ticket_data.get("attachments", []) or []
            if attachments:
                try:
                    contents = await ticket_client.fetch_attachment_content(attachments)
                    attachment_texts = [
                        item["text_content"]
                        for item in contents
                        if item.get("text_content") and not item.get("error")
                    ]
                except Exception as att_exc:
                    logger.warning("[EXTRACT] Attachment extraction failed for %s: %s", ticket_id, att_exc)

        return _project_raw_ticket(
            ticket_id=ticket_id,
            ticket_data=ticket_data,
            attachment_texts=attachment_texts,
            extraction_source=extraction_source,
        )
    except Exception as exc:
        logger.error("[EXTRACT] Failed for %s: %s", ticket_id, exc)
        return None


async def _fetch_tickets_from_source(
    ticket_ids: List[str],
    ticket_source: str,
    extract_attachments: bool = True,
) -> List[RawTicket]:
    resolved_source = normalize_ticket_source(ticket_source)
    extraction_source = _EXTRACTION_SOURCE_BY_TICKET_SOURCE.get(
        resolved_source,
        f"{resolved_source}_graph",
    )

    results: List[RawTicket] = []
    async with build_ticket_fetcher(
        source=resolved_source,
        verify_ssl=False,
    ) as ticket_client:
        for ticket_id in ticket_ids:
            ticket = await _extract_ticket(
                ticket_id=ticket_id,
                ticket_client=ticket_client,
                extract_attachments=extract_attachments,
                extraction_source=extraction_source,
            )
            if ticket:
                results.append(ticket)
    return results


def fetch_tickets(
    ticket_ids: List[str],
    ticket_source: str = "jira",
    extract_attachments: bool = True,
) -> List[RawTicket]:
    if not ticket_ids:
        raise ValueError("ticket_ids are required for historical ingestion")

    normalized_ids = _normalize_ticket_ids(ticket_ids)
    return asyncio.run(
        _fetch_tickets_from_source(
            normalized_ids,
            ticket_source=ticket_source,
            extract_attachments=extract_attachments,
        )
    )


def fetch_tickets_from_jira(
    ticket_ids: List[str],
    extract_attachments: bool = True,
) -> List[RawTicket]:
    return fetch_tickets(
        ticket_ids=ticket_ids,
        ticket_source="jira",
        extract_attachments=extract_attachments,
    )
