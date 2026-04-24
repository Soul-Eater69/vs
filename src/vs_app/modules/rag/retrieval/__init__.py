from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "retrieve_historical_support",
    "retrieve_semantic_candidates",
]

_MODULE_BY_EXPORT = {
    "retrieve_historical_support": "vs_app.modules.rag.retrieval.historical_retriever",
    "retrieve_semantic_candidates": "vs_app.modules.rag.retrieval.semantic_retriever",
}


def __getattr__(name: str) -> Any:
    module_name = _MODULE_BY_EXPORT.get(name)
    if not module_name:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value
