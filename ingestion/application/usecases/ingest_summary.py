"""
Summary ingestion pipeline → Azure AI Search Index B.

Responsibility: fetch → extract → summarize → resolve labels → embed → return.
Output is a TicketSummaryDocument — NOT chunks.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..content.retrieval_summary import format_structured_summary_text

from vs_app.integrations.jira.fetch_compat import get_ticket_data_compat
from ...adapters.embeddings import embed_batch
from ...domain.tickets.documents import TicketSummaryDocument

from ..extractor import consolidate_ticket_text
from ..summarizer import classify_ticket_value_streams, summarize_ticket

logger = logging.getLogger(__name__)


async def ingest_ticket_summary(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> TicketSummaryDocument:
    """Full summary-mode pipeline for a single ticket (fetch + extract + summarize + embed)."""
    cfg = _default_cfg(cfg)
    ticket_data = await get_ticket_data_compat(
        jira_client,
        ticket_key,
        config=cfg,
        llm_client=llm_client,
    )
    return await ingest_ticket_summary_payload(
        ticket_data=ticket_data,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
    )


async def ingest_ticket_summary_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> TicketSummaryDocument:
    """Process an already-fetched ticket payload (useful for batch jobs)."""
    cfg = _default_cfg(cfg)
    ticket_key = str(ticket_data.get("key", ""))

    consolidated_text = await consolidate_ticket_text(ticket_data, jira_client, cfg)
    logger.info("Consolidated %d chars for %s", len(consolidated_text), ticket_key)

    if llm_client is not None and not getattr(cfg, "skip_llm_summary", False):
        doc = summarize_ticket(ticket_key, consolidated_text, llm_client, cfg)
    else:
        doc = _heuristic_summary(ticket_key, ticket_data, consolidated_text)

    doc.value_stream_ids = list(ticket_data.get("value_stream_ids") or [])
    doc.value_stream_names = list(ticket_data.get("value_stream_names") or [])
    doc.label_source = str(
        ticket_data.get("value_stream_label_source")
        or ticket_data.get("label_source")
        or "jira_issuelinks"
    )
    doc.value_streams = classify_ticket_value_streams(
        ticket_id=ticket_key,
        consolidated_text=consolidated_text,
        value_stream_ids=doc.value_stream_ids,
        value_stream_names=doc.value_stream_names,
        label_source=doc.label_source,
        llm_client=llm_client,
        cfg=cfg,
    )
    doc.direct_vs_names = [
        row.get("vs_name", "")
        for row in doc.value_streams
        if row.get("inference_type") == "direct" and row.get("vs_name")
    ]
    doc.implied_vs_names = [
        row.get("vs_name", "")
        for row in doc.value_streams
        if row.get("inference_type") == "implied" and row.get("vs_name")
    ]

    if embedding_client is not None:
        doc.summary_embedding = _embed(
            format_structured_summary_text(doc), embedding_client, cfg
        )

    return doc


def _embed(text: str, embedding_client: Any, cfg: Any) -> list[float]:
    try:
        results = embed_batch(
            [text], embedding_client, model=getattr(cfg, "embedding_model", None)
        )
        return results[0] if results else []
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []


def _heuristic_summary(
    ticket_key: str,
    ticket_data: dict,
    consolidated_text: str,
) -> TicketSummaryDocument:
    fields = ticket_data.get("fields", {})
    summary_field = str(fields.get("summary") or ticket_key)
    preview = consolidated_text[:400] if consolidated_text else ""
    return TicketSummaryDocument(
        ticket_id=ticket_key,
        summary_text=f"{summary_field}. {preview}".strip(),
        business_problem=preview[:200],
        business_capability="",
        key_terms=[],
    )


def _default_cfg(cfg: Optional[Any]) -> Any:
    if cfg is not None:
        return cfg
    from pipelines.jira_batch.config import JiraIngestionConfig
    return JiraIngestionConfig()
