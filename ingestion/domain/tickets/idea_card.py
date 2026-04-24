"""
Idea-card resolution for a Jira ticket.

A Jira ticket may or may not have an idea card. When present, it can be:
  - an attachment (PPTX/PDF/DOCX)
  - a link embedded in the description (SharePoint URL, wiki-link, etc.)

When absent, the ticket body (description + comments) is used as the source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

IdeaCardOrigin = Literal["attachment", "description_link", "none"]
_SUPPORTED_CARD_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}


@dataclass
class IdeaCard:
    """Resolved primary source for a ticket."""

    origin: IdeaCardOrigin
    attachment: Optional[dict] = None
    file_bytes: Optional[bytes] = None
    filename: str = ""
    ext: str = ""
    source_url: str = ""

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
    """
    fields = ticket_data.get("fields", {}) or {}
    api_attachments = fields.get("attachment", []) or [
        att for att in (ticket_data.get("attachments", []) or [])
        if not att.get("is_description_link")
    ]
    description_links = ticket_data.get("description_attachments", []) or [
        att for att in (ticket_data.get("attachments", []) or [])
        if att.get("is_description_link")
    ]
    ticket_summary = str(fields.get("summary") or "")

    card = await _from_attachments(api_attachments, ticket_summary, jira_client)
    if card.is_present:
        return card

    card = await _from_description_links(
        fields.get("description"),
        jira_client,
        links=description_links,
    )
    if card.is_present:
        return card

    return IdeaCard(origin="none")


async def _from_attachments(
    attachments: list[dict],
    ticket_summary: str,
    jira_client: Any,
) -> IdeaCard:
    if not attachments:
        return IdeaCard(origin="none")

    from ...application.processing.attachment_routing import get_routing_candidates, route_attachments

    primary, _supporting, _quality, _artifact = route_attachments(
        attachments=attachments,
        ticket_summary=ticket_summary,
        download_fn=None,
    )

    candidate_pool = []
    if primary:
        candidate_pool.append(primary)
    candidate_pool.extend(get_routing_candidates(_artifact))
    if not candidate_pool:
        candidate_pool = list(attachments)

    seen_ids: set[str] = set()
    for candidate in candidate_pool:
        att_id = _attachment_id(candidate)
        if att_id in seen_ids:
            continue
        seen_ids.add(att_id)

        ext = str(candidate.get("ext") or _ext_of(candidate.get("filename", ""))).lower()
        if ext not in _SUPPORTED_CARD_EXTENSIONS:
            continue

        try:
            file_bytes = await jira_client.download_attachment(candidate)
        except Exception as exc:
            logger.info(
                "Idea-card candidate download skipped (%s): %s",
                candidate.get("filename"),
                exc,
            )
            continue

        if not file_bytes:
            continue

        return IdeaCard(
            origin="attachment",
            attachment=candidate,
            file_bytes=file_bytes,
            filename=str(candidate.get("filename") or ""),
            ext=ext,
        )

    return IdeaCard(origin="none")


async def _from_description_links(
    description: Any,
    jira_client: Any,
    *,
    links: Optional[list[dict]] = None,
) -> IdeaCard:
    from vs_app.integrations.files.description_links import extract_description_link_attachments

    links = list(links or extract_description_link_attachments(description))
    if not links:
        return IdeaCard(origin="none")

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


def _attachment_id(att: dict) -> str:
    return str(att.get("id") or att.get("attachment_id") or att.get("filename") or "")
