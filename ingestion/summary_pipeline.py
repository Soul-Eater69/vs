"""
Summary ingestion pipeline → Azure AI Search Index B.

Responsibility: fetch → extract → summarize → resolve labels → embed → return.

Output is a TicketSummaryDocument (Index B schema) — NOT chunks.
Chunking is an internal detail of the extractor, never exposed here.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..content.schemas import TicketSummaryDocument
from .extractor import consolidate_ticket_text
from .summarizer import classify_ticket_value_streams, summarize_ticket

logger = logging.getLogger(__name__)


async def ingest_ticket_summary(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> TicketSummaryDocument:
    """
    Full summary-mode pipeline for a single ticket.

    Steps:
      1. FETCH        — pull ticket data from Jira
      2. EXTRACT      — consolidate text (attachment + description + comments)
      3. SUMMARIZE    — LLM produces structured TicketSummaryDocument
      4. RESOLVE      — map VS labels from issue links
      5. EMBED        — encode summary_text to vector

    Returns a TicketSummaryDocument ready to upsert to Azure AI Search Index B.
    """
    cfg = _default_cfg(cfg)

    # 1. FETCH
    ticket_data = await jira_client.get_ticket_data(ticket_key, config=cfg)
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
    """
    Process an already-fetched ticket payload.
    Useful when ticket_data is pre-loaded (e.g. batch jobs).
    """
    cfg = _default_cfg(cfg)
    ticket_key = str(ticket_data.get("key", ""))

    # 2. EXTRACT — all text sources merged into one string
    consolidated_text = await consolidate_ticket_text(ticket_data, jira_client, cfg)
    logger.info("Consolidated %d chars for %s", len(consolidated_text), ticket_key)

    # 3. SUMMARIZE — LLM → structured fields
    if llm_client is not None and not getattr(cfg, "skip_llm_summary", False):
        doc = summarize_ticket(ticket_key, consolidated_text, llm_client, cfg)
    else:
        doc = _heuristic_summary(ticket_key, ticket_data, consolidated_text)

    # 4. RESOLVE — read precomputed VS labels from the fetch layer
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

    # 5. EMBED — full structured text → vector (richer than summary_text alone)
    if embedding_client is not None:
        doc.summary_embedding = _embed(_build_embedding_text(doc), embedding_client, cfg)

    return doc


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _build_embedding_text(doc: "TicketSummaryDocument") -> str:
    """Concatenate all structured fields for a richer embedding than summary_text alone."""
    parts = []
    if doc.summary_text:
        parts.append(doc.summary_text)
    if doc.business_problem:
        parts.append(f"Problem: {doc.business_problem}")
    if doc.business_capability:
        parts.append(f"Capability: {doc.business_capability}")
    if doc.key_terms:
        parts.append(f"Terms: {', '.join(doc.key_terms)}")
    return "\n".join(parts)


def _embed(text: str, embedding_client: Any, cfg: Any) -> list[float]:
    from clients.embedding import embed_batch
    try:
        results = embed_batch([text], embedding_client, model=getattr(cfg, "embedding_model", None))
        return results[0] if results else []
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Fallback when LLM is unavailable
# ---------------------------------------------------------------------------

def _heuristic_summary(
    ticket_key: str,
    ticket_data: dict,
    consolidated_text: str,
) -> TicketSummaryDocument:
    """Minimal summary from ticket metadata when LLM is skipped."""
    from content.schemas import TicketSummaryDocument

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
