"""Canonical ingestion command/result schemas."""

from __future__ import annotations

from dataclasses import dataclass

from .modes import IngestionMode, TicketSourceName


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


__all__ = ["IngestTicketCommand", "IngestTicketResult"]
