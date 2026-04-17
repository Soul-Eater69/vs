"""
Ingestion router.

Two modes, selected at call-time:

    mode="summary"  → TicketSummaryDocument     (Index B, for VS prediction)
    mode="chunks"   → HierarchicalTicketResult  (Index C, for RAG / evidence retrieval)

Both share the same upstream fetch and the same VS-label resolution; they
differ only in what the output shape represents.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from ..content.schemas import HierarchicalTicketResult, TicketSummaryDocument

IngestionMode = Literal["summary", "chunks"]
IngestionResult = Union[TicketSummaryDocument, HierarchicalTicketResult]


async def ingest_ticket(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    """Fetch a ticket and run the requested pipeline."""
    if mode == "summary":
        from ingestion.summary_pipeline import ingest_ticket_summary
        return await ingest_ticket_summary(
            ticket_key=ticket_key,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    if mode == "chunks":
        from ingestion.chunk_pipeline import ingest_ticket_chunks
        return await ingest_ticket_chunks(
            ticket_key=ticket_key,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    raise ValueError(f"Unknown ingestion mode: {mode!r}")


async def ingest_ticket_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    """Run the requested pipeline on an already-fetched ticket payload."""
    if mode == "summary":
        from ingestion.summary_pipeline import ingest_ticket_summary_payload
        return await ingest_ticket_summary_payload(
            ticket_data=ticket_data,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    if mode == "chunks":
        from ingestion.chunk_pipeline import ingest_ticket_chunks_payload
        return await ingest_ticket_chunks_payload(
            ticket_data=ticket_data,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    raise ValueError(f"Unknown ingestion mode: {mode!r}")
