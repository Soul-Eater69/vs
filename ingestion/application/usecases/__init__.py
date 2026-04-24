"""Legacy ingestion use-case exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "IngestionDeps": ("ingestion.application.usecases.ingest_ticket", "IngestionDeps"),
    "IngestionMode": ("ingestion.application.usecases.ingest_ticket", "IngestionMode"),
    "IngestionResult": ("ingestion.application.usecases.ingest_ticket", "IngestionResult"),
    "TicketIngestionContext": ("ingestion.application.usecases.ingest_ticket", "TicketIngestionContext"),
    "ingest_one_ticket": ("ingestion.application.usecases.ingest_ticket", "ingest_one_ticket"),
    "ingest_single_ticket": ("ingestion.application.usecases.ingest_ticket", "ingest_single_ticket"),
    "ingest_ticket": ("ingestion.application.usecases.ingest_ticket", "ingest_ticket"),
    "ingest_ticket_chunks": ("ingestion.application.usecases.ingest_chunks", "ingest_ticket_chunks"),
    "ingest_ticket_chunks_payload": ("ingestion.application.usecases.ingest_chunks", "ingest_ticket_chunks_payload"),
    "ingest_ticket_payload": ("ingestion.application.usecases.ingest_ticket", "ingest_ticket_payload"),
    "ingest_ticket_summary": ("ingestion.application.usecases.ingest_summary", "ingest_ticket_summary"),
    "ingest_ticket_summary_payload": ("ingestion.application.usecases.ingest_summary", "ingest_ticket_summary_payload"),
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
