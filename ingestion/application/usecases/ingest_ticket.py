"""
Ingestion router and service wrapper.

ingest_ticket / ingest_ticket_payload — choose summary or chunks mode.
TicketIngestionContext / ingest_single_ticket — service-layer convenience wrappers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

from ...domain.tickets.documents import HierarchicalTicketResult, TicketSummaryDocument

logger = logging.getLogger(__name__)

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
        from .ingest_summary import ingest_ticket_summary
        return await ingest_ticket_summary(
            ticket_key=ticket_key,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    if mode == "chunks":
        from .ingest_chunks import ingest_ticket_chunks
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
        from .ingest_summary import ingest_ticket_summary_payload
        return await ingest_ticket_summary_payload(
            ticket_data=ticket_data,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    if mode == "chunks":
        from .ingest_chunks import ingest_ticket_chunks_payload
        return await ingest_ticket_chunks_payload(
            ticket_data=ticket_data,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            cfg=cfg,
        )
    raise ValueError(f"Unknown ingestion mode: {mode!r}")


# ---------------------------------------------------------------------------
# Service wrapper
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TicketIngestionContext:
    jira_client: Any
    llm_client: Any | None = None
    embedding_client: Any | None = None


IngestionDeps = TicketIngestionContext


async def ingest_single_ticket(
    ticket_id: str,
    deps: TicketIngestionContext,
    cfg: Any,
    storage_dir: Optional[str] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    from ..service import TicketExtractionService

    extraction_service = TicketExtractionService(ticket_client=deps.jira_client)
    ticket_data = await extraction_service.extract_ticket(ticket_id, cfg)

    if not isinstance(ticket_data, dict):
        raise RuntimeError(
            f"Ticket {ticket_id} extraction did not return a dict. Got: {type(ticket_data)}"
        )
    if "key" not in ticket_data:
        raise RuntimeError(f"Ticket {ticket_id} extraction missing 'key'.")

    if storage_dir is not None:
        logger.warning(
            "storage_dir=%s is ignored — artifact persistence is handled by the pipeline config",
            storage_dir,
        )

    return await ingest_ticket_payload(
        ticket_data=ticket_data,
        jira_client=deps.jira_client,
        llm_client=deps.llm_client,
        embedding_client=deps.embedding_client,
        cfg=cfg,
        mode=mode,
    )


async def ingest_one_ticket(
    ticket_id: str,
    deps: TicketIngestionContext,
    cfg: Any,
    storage_dir: Optional[str] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    return await ingest_single_ticket(
        ticket_id=ticket_id,
        deps=deps,
        cfg=cfg,
        storage_dir=storage_dir,
        mode=mode,
    )
