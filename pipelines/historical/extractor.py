"""
Jira-backed ticket extraction for the historical pipeline.

Fetches live ticket data through the jira client and assembles
lightweight text representations without running the full ingestion pipeline.

Produces RawTicket records with:
  - raw_text (retrieval text from description + attachments + comments)
  - description (cleaned description)
  - value_stream_labels (canonical VS names from issue links)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from ...jira.text.text_assembly import (
    build_retrieval_text,
    clean_description,
    extract_comment_texts,
)
from .models import RawTicket

logger = logging.getLogger(__name__)
EXTRACTION_SOURCE = "jira_direct"


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
) -> RawTicket:
    """Project Jira ticket data into a RawTicket for enrichment."""
    fields = ticket_data.get("fields", {}) or {}

    # Clean description (handles ADF dicts and wiki markup strings)
    description = clean_description(fields.get("description"))

    # Title
    title = str(fields.get("summary") or ticket_id)

    # Value stream labels (already resolved by JiraValueStreamClient.get_ticket_data)
    vs_labels = _dedupe_labels(ticket_data.get("value_stream_names"))

    # Comments
    comment_texts = extract_comment_texts(fields.get("comment") or {})

    # Assemble retrieval text
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
        extraction_source=EXTRACTION_SOURCE,
        char_count=len(raw_text),
    )


async def _extract_ticket(
    ticket_id: str,
    jira_client: Any,
    config: Any = None,
    extract_attachments: bool = True,
) -> Optional[RawTicket]:
    """Fetch and extract a single ticket."""
    try:
        ticket_data = await jira_client.get_ticket_data(ticket_id, config=config)

        # Extract attachment text if requested and client supports it
        attachment_texts: List[str] = []
        if extract_attachments:
            attachments = ticket_data.get("attachments", []) or []
            if attachments:
                try:
                    contents = await jira_client.fetch_attachment_content(attachments)
                    attachment_texts = [
                        c["text_content"]
                        for c in contents
                        if c.get("text_content") and not c.get("error")
                    ]
                except Exception as att_exc:
                    logger.warning("[EXTRACT] Attachment extraction failed for %s: %s", ticket_id, att_exc)

        return _project_raw_ticket(
            ticket_id=ticket_id,
            ticket_data=ticket_data,
            attachment_texts=attachment_texts,
        )
    except Exception as exc:
        logger.error("[EXTRACT] Failed for %s: %s", ticket_id, exc)
        return None


async def _fetch_tickets_from_jira(
    ticket_ids: List[str],
    base_url: str,
    token: str,
    sharepoint_client: Any = None,
    extract_attachments: bool = True,
) -> List[RawTicket]:
    from jira.ticket_client import JiraTicketClient

    results: List[RawTicket] = []
    async with JiraTicketClient(
        base_url=base_url,
        token=token,
        verify_ssl=False,
        sharepoint_client=sharepoint_client,
    ) as jira_client:
        for ticket_id in ticket_ids:
            ticket = await _extract_ticket(
                ticket_id=ticket_id,
                jira_client=jira_client,
                extract_attachments=extract_attachments,
            )
            if ticket:
                results.append(ticket)
    return results


def fetch_tickets_from_jira(
    ticket_ids: List[str],
    sharepoint_client: Any = None,
    extract_attachments: bool = True,
) -> List[RawTicket]:
    """Fetch tickets from Jira and project them into RawTicket records.

    Args:
        ticket_ids: Jira ticket keys to fetch.
        sharepoint_client: Optional SharePointClient for attachments hosted on SharePoint.
        extract_attachments: Whether to download and extract attachment text (default True).
    """
    from config import JIRA_BASE_URL, JIRA_TOKEN

    if not ticket_ids:
        raise ValueError("ticket_ids are required for historical ingestion")
    if not JIRA_BASE_URL or not JIRA_TOKEN:
        raise RuntimeError("JIRA_BASE_URL and JIRA_TOKEN must be set")

    normalized_ids = _normalize_ticket_ids(ticket_ids)
    return asyncio.run(
        _fetch_tickets_from_jira(
            normalized_ids,
            JIRA_BASE_URL,
            JIRA_TOKEN,
            sharepoint_client=sharepoint_client,
            extract_attachments=extract_attachments,
        )
    )
