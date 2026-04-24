"""Historical ticket enrichment pipeline."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["run_historical_ingestion"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module("vs_app.modules.rag.corpus.corpus_indexer")
    value = getattr(module, name)
    globals()[name] = value
    return value
