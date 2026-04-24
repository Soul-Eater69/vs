"""Canonical ingestion package exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "IngestionDeps": ("vs_app.modules.ingestion.pipeline", "IngestionDeps"),
    "IngestionMode": ("vs_app.modules.ingestion.modes", "IngestionMode"),
    "IngestionResult": ("vs_app.modules.ingestion.pipeline", "IngestionResult"),
    "IngestTicketCommand": ("vs_app.modules.ingestion.schemas", "IngestTicketCommand"),
    "IngestTicketResult": ("vs_app.modules.ingestion.schemas", "IngestTicketResult"),
    "IngestionService": ("vs_app.modules.ingestion.service", "IngestionService"),
    "TicketExtractionService": ("vs_app.modules.ingestion.service", "TicketExtractionService"),
    "TicketIngestionContext": ("vs_app.modules.ingestion.pipeline", "TicketIngestionContext"),
    "TicketSourceName": ("vs_app.modules.ingestion.modes", "TicketSourceName"),
    "ingest_one_ticket": ("vs_app.modules.ingestion.pipeline", "ingest_one_ticket"),
    "ingest_single_ticket": ("vs_app.modules.ingestion.pipeline", "ingest_single_ticket"),
    "ingest_ticket": ("vs_app.modules.ingestion.pipeline", "ingest_ticket"),
    "ingest_ticket_payload": ("vs_app.modules.ingestion.pipeline", "ingest_ticket_payload"),
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
