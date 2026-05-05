"""Standalone ingestion package exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "IngestionResult": ("vs_app.ingestion.pipeline", "IngestionResult"),
    "ingest_ticket": ("vs_app.ingestion.pipeline", "ingest_ticket"),
    "ingest_ticket_payload": ("vs_app.ingestion.pipeline", "ingest_ticket_payload"),
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
