"""Orchestrate the metadata-only idea-card audit pipeline for a single ticket."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .accessibility import probe_link
from .candidate_builder import build_attachment_candidates
from .decision import build_final_recommendation, compare_decisions
from .deterministic_classifier import classify_fuzzy
from .link_extractor import extract_description_candidates
from .llm_classifier import classify_with_llm
from .models import (
    AttachmentSummary,
    DescriptionSignals,
    FuzzyDecision,
    LinkAccessStatus,
    LinkSummary,
    TicketAuditRecord,
)

logger = logging.getLogger(__name__)

_INACCESSIBLE_STATUSES = {
    "auth_required", "forbidden", "sharepoint_auth_required",
    "timeout", "connection_error", "not_found", "unknown_error",
}


def _build_link_summary(links: list) -> LinkSummary:
    total = len(links)
    accessible = sum(1 for lnk in links if lnk.access_status == LinkAccessStatus.accessible)
    inaccessible = sum(
        1 for lnk in links if lnk.access_status.value in _INACCESSIBLE_STATUSES
    )
    # Use LLM usefulness if available, otherwise fuzzy
    useful = sum(
        1 for lnk in links
        if (lnk.is_useful_llm if lnk.is_useful_llm is not None else lnk.is_useful_fuzzy)
    )
    return LinkSummary(
        total_links=total,
        useful_links=useful,
        not_useful_links=total - useful,
        accessible_links=accessible,
        inaccessible_links=inaccessible,
    )


def _build_attachment_summary(
    attachments: list,
    total_raw: int,
) -> AttachmentSummary:
    _DECK_EXTS = {"pptx", "ppt", "pdf", "docx", "doc"}
    candidates = sum(
        1 for att in attachments
        if att.is_useful_fuzzy or att.is_useful_llm is True
    )
    ppt_or_deck = sum(1 for att in attachments if att.extension in _DECK_EXTS)
    return AttachmentSummary(
        total_attachments=total_raw,
        candidate_attachments=candidates,
        ppt_or_deck_like_count=ppt_or_deck,
    )


async def audit_ticket(
    ticket_id: str,
    ticket_client: Any,
    *,
    probe_links: bool = True,
    enable_llm: bool = True,
    llm_every_ticket: bool = True,
    llm_client: Optional[Any] = None,
) -> TicketAuditRecord:
    """
    Metadata-only idea-card audit pipeline for one ticket.

    Steps:
      1. Fetch ticket from Neo4j
      2. Extract description links + statements
      3. Probe links (optional — checks access, not content)
      4. Build attachment candidates (metadata-only, no download)
      5. Fuzzy classification (sets is_useful_fuzzy on candidates)
      6. LLM classification (independent, clean input, sets is_useful_llm on candidates)
      7. Compare fuzzy vs LLM
      8. Build final recommendation
      9. Assemble TicketAuditRecord
    """
    errors: list[str] = []

    # --- 1. Fetch ticket ---
    try:
        ticket_data = await ticket_client.get_ticket_data(ticket_id)
    except Exception as exc:
        logger.error("Failed to fetch ticket %s: %s", ticket_id, exc)
        fuzzy_error = FuzzyDecision(
            presence="absent", confidence=0.0, reasons=[f"Fetch error: {exc}"]
        )
        from .models import FinalRecommendation
        return TicketAuditRecord(
            ticket_id=ticket_id,
            fuzzy_decision=fuzzy_error,
            final_recommendation=FinalRecommendation(presence="absent"),
            errors=[f"Fetch error: {exc}"],
        )

    fields = ticket_data.get("fields") or {}
    title = str(fields.get("summary") or ticket_id)
    description = str(fields.get("description") or "")
    raw_attachments: list[dict[str, Any]] = list(ticket_data.get("attachments") or [])

    linked_vs = ticket_data.get("linked_value_streams") or []
    value_streams: list[str] = []
    for vs in linked_vs:
        if isinstance(vs, dict):
            name = str(vs.get("entity_name") or vs.get("name") or "").strip()
            if name:
                value_streams.append(name)
        elif isinstance(vs, str) and vs.strip():
            value_streams.append(vs.strip())

    # --- 2. Extract links and statements ---
    links, statements = extract_description_candidates(description)

    # --- 3. Probe links (access check only, no content download) ---
    if probe_links and links:
        try:
            probed = await asyncio.gather(*[probe_link(lnk) for lnk in links])
            links = list(probed)
        except Exception as exc:
            logger.warning("Link probing failed for %s: %s", ticket_id, exc)
            errors.append(f"Link probe error: {exc}")

    # --- 4. Build attachment candidates (metadata-only) ---
    try:
        attachments = build_attachment_candidates(
            raw_attachments,
            ticket_summary=f"{title}\n{description[:200]}",
        )
    except Exception as exc:
        logger.warning("Attachment candidate build failed for %s: %s", ticket_id, exc)
        attachments = []
        errors.append(f"Attachment candidate error: {exc}")

    # --- 5. Fuzzy classification (mutates link is_useful_fuzzy fields) ---
    fuzzy_decision = classify_fuzzy(statements, links, attachments)

    # --- 6. LLM classification (independent, clean metadata, no fuzzy scores) ---
    llm_decision = None
    llm_error: Optional[str] = None
    if enable_llm and llm_client is not None:
        try:
            llm_decision = classify_with_llm(
                ticket_id=ticket_id,
                title=title,
                description=description,
                statements=statements,
                value_streams=value_streams,
                links=links,
                attachments=attachments,
                llm_client=llm_client,
                fuzzy=fuzzy_decision,
                llm_every_ticket=llm_every_ticket,
            )
        except Exception as exc:
            logger.warning("[%s] LLM classification error: %s", ticket_id, exc)
            llm_error = str(exc)
            errors.append(f"LLM error: {exc}")

    # --- 7. Compare ---
    comparison = None
    if llm_decision is not None:
        comparison = compare_decisions(fuzzy_decision, llm_decision)

    # --- 8. Final recommendation ---
    final_recommendation = build_final_recommendation(
        fuzzy=fuzzy_decision,
        llm=llm_decision,
        comparison=comparison,
        links=links,
        attachments=attachments,
        statements=statements,
    )

    # --- 9. Assemble record ---
    description_signals = DescriptionSignals(
        has_idea_card_statement=bool(statements),
        statements=statements,
        description_excerpt=description[:400],
    )
    link_summary = _build_link_summary(links)
    attachment_summary = _build_attachment_summary(attachments, len(raw_attachments))

    return TicketAuditRecord(
        ticket_id=ticket_id,
        title=title,
        value_streams=value_streams,
        metadata_only=True,
        description_signals=description_signals,
        link_summary=link_summary,
        links=links,
        attachment_summary=attachment_summary,
        attachments=attachments,
        fuzzy_decision=fuzzy_decision,
        llm_decision=llm_decision,
        llm_error=llm_error,
        comparison=comparison,
        final_recommendation=final_recommendation,
        errors=errors,
    )
