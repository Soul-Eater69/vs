"""Thin public ingestion wrappers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from vs_app.modules.tickets.documents import TicketSummaryDocument

from .summary.pipeline import ingest_ticket_summary, ingest_ticket_summary_payload

IngestionResult = TicketSummaryDocument
Progress = Callable[[str], None]


async def ingest_ticket(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    progress: Progress | None = None,
) -> IngestionResult:
    return await ingest_ticket_summary(
        ticket_key=ticket_key,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
        progress=progress,
    )


async def ingest_ticket_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
    progress: Progress | None = None,
) -> IngestionResult:
    return await ingest_ticket_summary_payload(
        ticket_data=ticket_data,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
        progress=progress,
    )


__all__ = [
    "IngestionResult",
    "ingest_ticket",
    "ingest_ticket_payload",
]
