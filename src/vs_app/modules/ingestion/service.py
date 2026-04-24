"""Ingestion service facade.

Orchestrates: ticket source selection -> normalized ticket -> summary/chunk
pipelines -> optional debug persistence. Source-agnostic; no Jira/Neo4j imports.
"""

from __future__ import annotations

from typing import Any

from .modes import IngestionMode, TicketSourceName
from .schemas import IngestTicketCommand, IngestTicketResult


class TicketExtractionService:
    """Facade over a ticket client; bridges use cases to fetch compatibility helpers."""

    def __init__(self, ticket_client: Any | None = None, jira_client: Any | None = None) -> None:
        self._ticket_client = ticket_client if ticket_client is not None else jira_client
        if self._ticket_client is None:
            raise ValueError("A ticket client is required")

    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        from vs_app.integrations.jira.fetch_compat import get_ticket_data_compat

        return await get_ticket_data_compat(self._ticket_client, ticket_id, config=cfg)


class IngestionService:
    """Builds a ticket source, runs summary/chunk pipelines, returns results."""

    def __init__(
        self,
        ticket_source_factory: Any,
        summary_pipeline: Any,
        chunk_pipeline: Any,
        debug_writer: Any | None = None,
    ) -> None:
        self.ticket_source_factory = ticket_source_factory
        self.summary_pipeline = summary_pipeline
        self.chunk_pipeline = chunk_pipeline
        self.debug_writer = debug_writer

    async def ingest_ticket(self, command: IngestTicketCommand) -> IngestTicketResult:
        async with self.ticket_source_factory.build(command.source) as ticket_source:
            ticket = await ticket_source.get_ticket(command.ticket_id)

        summary_doc: dict | None = None
        chunk_docs: list[dict] | None = None

        if command.mode in ("summary", "both"):
            summary = await self.summary_pipeline.run(ticket)
            summary_doc = (
                summary.to_index_doc() if hasattr(summary, "to_index_doc") else summary
            )

        if command.mode in ("chunks", "both"):
            chunks = await self.chunk_pipeline.run(ticket)
            chunk_docs = (
                chunks.all_documents() if hasattr(chunks, "all_documents") else chunks
            )

        return IngestTicketResult(
            ticket_id=command.ticket_id,
            summary=summary_doc,
            chunks=chunk_docs,
            errors=[],
        )


__all__ = [
    "IngestionMode",
    "IngestTicketCommand",
    "IngestTicketResult",
    "IngestionService",
    "TicketExtractionService",
    "TicketSourceName",
]
