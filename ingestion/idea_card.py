"""
Idea-card resolution for a Jira ticket.

A Jira ticket may or may not have an idea card. When present, it can be:
  - an attachment (PPTX/PDF/DOCX)
  - a link embedded in the description (SharePoint URL, wiki-link, etc.)

When absent, the ticket body (description + comments) is used as the source.

This module resolves the idea card source once, so both the chunk and
summary pipelines operate on the same notion of "primary source".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

IdeaCardOrigin = Literal["attachment", "description_link", "none"]


@dataclass
class IdeaCard:
    """Resolved primary source for a ticket."""

    origin: IdeaCardOrigin
    attachment: Optional[dict] = None    # routing artifact for the primary attachment
    file_bytes: Optional[bytes] = None   # downloaded bytes, if available
    filename: str = ""
    ext: str = ""
    source_url: str = ""                 # for description_link origin

    @property
    def is_present(self) -> bool:
        return self.origin != "none" and self.file_bytes is not None


async def resolve_idea_card(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> IdeaCard:
    """
    Try to resolve an idea card in this order:
      1. Primary attachment (via routing)
      2. Description-embedded link (SharePoint URL, etc.)
      3. None

    Always returns an IdeaCard; check `.is_present` before using file_bytes.
    """
    fields = ticket_data.get("fields", {}) or {}
    attachments = ticket_data.get("attachments", []) or fields.get("attachment", []) or []
    ticket_summary = str(fields.get("summary") or "")

    card = await _from_attachments(attachments, ticket_summary, jira_client)
    if card.is_present:
        return card

    return await _from_description_links(fields.get("description"), jira_client)


async def _from_attachments(
    attachments: list[dict],
    ticket_summary: str,
    jira_client: Any,
) -> IdeaCard:
    if not attachments:
        return IdeaCard(origin="none")

    from processing.attachment_routing import route_attachments

    async def _download(att: dict) -> bytes:
        return await jira_client.download_attachment(att)

    # route_attachments expects a sync download_fn; we pre-download candidates in Layer2
    # via a small adapter that blocks only because route_attachments is already sync.
    # Simpler path: run triage without peek/extract, then manually download the picked primary.
    primary, _supporting, _quality, _artifact = route_attachments(
        attachments=attachments,
        ticket_summary=ticket_summary,
        download_fn=None,
    )
    if not primary:
        return IdeaCard(origin="none")

    try:
        file_bytes = await jira_client.download_attachment(primary)
    except Exception as exc:
        logger.warning("Primary attachment download failed: %s", exc)
        return IdeaCard(origin="none")

    if not file_bytes:
        return IdeaCard(origin="none")

    return IdeaCard(
        origin="attachment",
        attachment=primary,
        file_bytes=file_bytes,
        filename=str(primary.get("filename") or ""),
        ext=str(primary.get("ext") or _ext_of(primary.get("filename", ""))),
    )


async def _from_description_links(description: Any, jira_client: Any) -> IdeaCard:
    from jira.attachments.description_links import extract_description_link_attachments

    links = extract_description_link_attachments(description)
    if not links:
        return IdeaCard(origin="none")

    # Prefer links that look like extractable docs (pptx/pdf/docx)
    priority = {"pptx": 0, "ppt": 1, "pdf": 2, "docx": 3, "doc": 4}
    links_sorted = sorted(
        links,
        key=lambda a: priority.get(_ext_of(a.get("filename", "")), 99),
    )

    for link in links_sorted:
        ext = _ext_of(link.get("filename", ""))
        if ext not in priority:
            continue
        try:
            file_bytes = await jira_client.download_attachment(link)
        except Exception as exc:
            logger.info("Description-link download skipped (%s): %s", link.get("content"), exc)
            continue
        if file_bytes:
            return IdeaCard(
                origin="description_link",
                attachment=link,
                file_bytes=file_bytes,
                filename=str(link.get("filename") or ""),
                ext=ext,
                source_url=str(link.get("content") or ""),
            )

    return IdeaCard(origin="none")


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""
