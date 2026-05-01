"""LLM enrichment: summarize tickets + classify VS as direct/implied."""

from __future__ import annotations

import logging
from typing import List

from vs_app.modules.prompts.loader import (
    build_historical_enrichment_prompt,
    build_historical_enrichment_system_prompt,
)

from ..query.views import clean_ppt_text
from .corpus_models import (
    EnrichedTicket,
    InferenceType,
    RawTicket,
    TicketEnrichmentResult,
    VSAttachment,
)

logger = logging.getLogger(__name__)

ENRICHMENT_MODEL = "gpt-5-idp"


def _build_prompt(raw_text: str, vs_labels: List[str]) -> str:
    cleaned = clean_ppt_text(raw_text)[:6000]
    vs_list = "\n".join(f"- {name}" for name in vs_labels) if vs_labels else "(none)"
    return build_historical_enrichment_prompt(raw_text=cleaned, vs_list=vs_list)


def enrich_one(ticket: RawTicket, model: str = ENRICHMENT_MODEL) -> EnrichedTicket:
    from vs_app.integrations.clients.llm import IDPChatOpenAI

    base = EnrichedTicket(
        ticket_id=ticket.ticket_id,
        title=ticket.title,
        raw_text_preview=clean_ppt_text(ticket.raw_text)[:2000],
        value_stream_labels=ticket.value_stream_labels,
        extraction_source=ticket.extraction_source,
        enrichment_model=model,
        char_count=ticket.char_count or len(ticket.raw_text),
    )

    if not ticket.raw_text or len(ticket.raw_text.strip()) < 50:
        logger.warning("[ENRICH] %s: text too short, skipping LLM", ticket.ticket_id)
        base.enrichment_status = "failed"
        base.summary = ticket.title
        return base

    try:
        llm = IDPChatOpenAI(model=model)
        structured = llm.with_structured_output(TicketEnrichmentResult, method="function_calling")
        result: TicketEnrichmentResult = structured.invoke([
            {"role": "system", "content": build_historical_enrichment_system_prompt()},
            {"role": "user", "content": _build_prompt(ticket.raw_text, ticket.value_stream_labels)},
        ])

        base.summary = result.summary
        base.domain_tags = result.domain_tags
        base.enrichment_status = "enriched"

        for item in result.vs_classifications:
            base.value_streams.append(VSAttachment(
                vs_name=item.vs_name, inference_type=item.inference_type, reason=item.reason,
            ))

        direct = sum(1 for value in base.value_streams if value.inference_type == InferenceType.DIRECT)
        logger.info(
            "[ENRICH] %s: %d VS (%d direct, %d implied)",
            ticket.ticket_id,
            len(base.value_streams),
            direct,
            len(base.value_streams) - direct,
        )

    except Exception as exc:
        logger.error("[ENRICH] %s failed: %s", ticket.ticket_id, exc)
        base.enrichment_status = "failed"
        base.summary = ticket.title

    return base


def enrich_batch(tickets: List[RawTicket], model: str = ENRICHMENT_MODEL) -> List[EnrichedTicket]:
    results = []
    for idx, ticket in enumerate(tickets, 1):
        logger.info("[ENRICH] %d/%d: %s", idx, len(tickets), ticket.ticket_id)
        results.append(enrich_one(ticket, model=model))

    ok = sum(1 for value in results if value.enrichment_status == "enriched")
    logger.info("[ENRICH] Batch complete: %d/%d enriched", ok, len(results))
    return results
