"""Ingestion service facade.

Orchestrates: ticket source selection -> normalized ticket -> summary/chunk
pipelines -> optional debug persistence. Source-agnostic; no Jira/Neo4j imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

IngestionMode = Literal["summary", "chunks", "both"]
TicketSourceName = Literal["jira", "neo4j"]


@dataclass(slots=True)
class IngestTicketCommand:
    ticket_id: str
    source: TicketSourceName = "jira"
    mode: IngestionMode = "both"
    force: bool = False
    persist_debug: bool = False


@dataclass(slots=True)
class IngestTicketResult:
    ticket_id: str
    summary: dict | None = None
    chunks: list[dict] | None = None
    errors: list[str] | None = None


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
