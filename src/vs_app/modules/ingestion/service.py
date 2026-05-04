"""Ingestion service facade.

Orchestrates: Jira ticket selection -> normalized ticket -> summary pipeline.
"""

from __future__ import annotations

from typing import Any

from .modes import IngestionMode, TicketSourceName
from .schemas import IngestTicketCommand, IngestTicketResult


class TicketExtractionService:
    """Facade over a ticket client; bridges use cases to ticket fetching."""

    def __init__(self, ticket_client: Any | None = None, jira_client: Any | None = None) -> None:
        self._ticket_client = ticket_client if ticket_client is not None else jira_client
        if self._ticket_client is None:
            raise ValueError("A ticket client is required")

    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        return await self._ticket_client.get_ticket_data(ticket_id, config=cfg)


class IngestionService:
    """Builds a ticket source, runs summary ingestion, returns results."""

    def __init__(
        self,
        ticket_source_factory: Any,
        summary_pipeline: Any,
        debug_writer: Any | None = None,
    ) -> None:
        self.ticket_source_factory = ticket_source_factory
        self.summary_pipeline = summary_pipeline
        self.debug_writer = debug_writer

    async def ingest_ticket(self, command: IngestTicketCommand) -> IngestTicketResult:
        if command.mode != "summary":
            raise ValueError(f"Unsupported ingestion mode: {command.mode!r}")

        async with self.ticket_source_factory.build(command.source) as ticket_source:
            ticket = await ticket_source.get_ticket(command.ticket_id)

        summary = await self.summary_pipeline.run(ticket)
        summary_doc = (
            summary.to_index_doc() if hasattr(summary, "to_index_doc") else summary
        )

        return IngestTicketResult(
            ticket_id=command.ticket_id,
            summary=summary_doc,
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
