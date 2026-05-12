"""Standalone ingestion package exports."""

from __future__ import annotations

from vs_app.ingestion.summary.pipeline import ingest_ticket_summary, ingest_ticket_summary_payload
from vs_app.modules.tickets.documents import TicketSummaryDocument

IngestionResult = TicketSummaryDocument
ingest_ticket = ingest_ticket_summary
ingest_ticket_payload = ingest_ticket_summary_payload

__all__ = [
    "IngestionResult",
    "ingest_ticket",
    "ingest_ticket_payload",
]
