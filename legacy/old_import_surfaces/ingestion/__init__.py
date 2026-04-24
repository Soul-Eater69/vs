"""Legacy ingestion package exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "IdeaCard": ("ingestion.domain.tickets.idea_card", "IdeaCard"),
    "IngestionDeps": ("ingestion.application.usecases", "IngestionDeps"),
    "IngestionMode": ("ingestion.application.usecases", "IngestionMode"),
    "IngestionResult": ("ingestion.application.usecases", "IngestionResult"),
    "TicketIngestionContext": ("ingestion.application.usecases", "TicketIngestionContext"),
    "ingest_one_ticket": ("ingestion.application.usecases", "ingest_one_ticket"),
    "ingest_single_ticket": ("ingestion.application.usecases", "ingest_single_ticket"),
    "ingest_ticket": ("ingestion.application.usecases", "ingest_ticket"),
    "ingest_ticket_chunks": ("ingestion.application.usecases", "ingest_ticket_chunks"),
    "ingest_ticket_chunks_payload": ("ingestion.application.usecases", "ingest_ticket_chunks_payload"),
    "ingest_ticket_payload": ("ingestion.application.usecases", "ingest_ticket_payload"),
    "ingest_ticket_summary": ("ingestion.application.usecases", "ingest_ticket_summary"),
    "ingest_ticket_summary_payload": ("ingestion.application.usecases", "ingest_ticket_summary_payload"),
    "resolve_idea_card": ("ingestion.domain.tickets.idea_card", "resolve_idea_card"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
