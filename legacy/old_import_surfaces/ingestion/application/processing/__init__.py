"""Legacy processing package exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "build_routing_artifact": (
        "ingestion.application.processing.attachment_routing",
        "build_routing_artifact",
    ),
    "get_routing_candidates": (
        "ingestion.application.processing.attachment_routing",
        "get_routing_candidates",
    ),
    "route_attachments": (
        "ingestion.application.processing.attachment_routing",
        "route_attachments",
    ),
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
