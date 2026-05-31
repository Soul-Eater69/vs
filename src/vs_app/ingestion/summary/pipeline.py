"""Canonical summary ingestion pipeline."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Optional

from vs_app.integrations.embeddings.client import embed_batch
from vs_app.modules.tickets.documents import TicketSummaryDocument

from vs_app.ingestion.extraction.text_consolidator import consolidate_ticket_text

from .llm_summary_extractor import classify_ticket_value_streams, summarize_ticket
from .mapper import format_structured_summary_text

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]


async def ingest_ticket_summary(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    progress: Progress | None = None,
) -> TicketSummaryDocument:
    """Full summary-mode pipeline for a single ticket."""
    cfg = _default_cfg(cfg)
    _require_llm_ingestion(ticket_key, llm_client, cfg)
    ticket_data = await jira_client.get_ticket_data(
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
        progress=progress,
    )


async def ingest_ticket_summary_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    progress: Progress | None = None,
) -> TicketSummaryDocument:
    """Process an already-fetched ticket payload."""
    cfg = _default_cfg(cfg)
    ticket_key = str(ticket_data.get("key", ""))
    _require_llm_ingestion(ticket_key, llm_client, cfg)

    consolidated_text = await consolidate_ticket_text(
        ticket_data,
        jira_client,
        cfg,
        progress=progress,
    )
    logger.info("Consolidated %d chars for %s", len(consolidated_text), ticket_key)

    doc = await asyncio.to_thread(
        summarize_ticket,
        ticket_key,
        consolidated_text,
        llm_client,
        cfg,
    )

    doc.value_stream_ids = list(ticket_data.get("value_stream_ids") or [])
    doc.value_stream_names = list(ticket_data.get("value_stream_names") or [])
    doc.jira_group_ids = list(ticket_data.get("jira_group_ids") or [])
    doc.label_source = str(
        ticket_data.get("value_stream_label_source")
        or ticket_data.get("label_source")
        or "jira_issuelinks"
    )
    doc.value_streams = await asyncio.to_thread(
        classify_ticket_value_streams,
        ticket_id=ticket_key,
        consolidated_text=consolidated_text,
        value_stream_ids=doc.value_stream_ids,
        value_stream_names=doc.value_stream_names,
        jira_group_ids=doc.jira_group_ids,
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
        doc.summary_embedding = await asyncio.to_thread(
            _embed,
            format_structured_summary_text(doc),
            embedding_client,
            cfg,
        )

    return doc


def _embed(text: str, embedding_client: Any, cfg: Any) -> list[float]:
    try:
        results = embed_batch(
            [text], embedding_client, model=getattr(cfg, "embedding_model", None)
        )
    except Exception as exc:
        raise RuntimeError(f"Embedding failed: {exc}") from exc

    if not results or not results[0]:
        raise RuntimeError("Embedding returned empty result")

    return results[0]


def _require_llm_ingestion(ticket_key: str, llm_client: Any | None, cfg: Any) -> None:
    if llm_client is None:
        raise RuntimeError(
            "LLM client is required for summary ingestion; refusing to index "
            f"weak fallback summary for {ticket_key}"
        )

    if getattr(cfg, "skip_llm_summary", False):
        raise RuntimeError(
            f"skip_llm_summary=True is not allowed for historical RAG indexing: {ticket_key}"
        )


def _default_cfg(cfg: Optional[Any]) -> Any:
    if cfg is not None:
        return cfg
    from vs_app.jobs.jira_batch.config import JiraIngestionConfig

    return JiraIngestionConfig()


__all__ = ["ingest_ticket_summary", "ingest_ticket_summary_payload"]
