"""Compatibility layer for legacy triage imports.

Use ingestion.attachment_routing instead.
"""

from ingestion.attachment_routing import (
    _layer0_filter,
    build_routing_artifact,
    build_triage_artifact,
    get_chunking_candidates,
    get_routing_candidates,
    route_attachments,
    triage_attachments,
)

__all__ = [
    "route_attachments",
    "get_routing_candidates",
    "build_routing_artifact",
    "_layer0_filter",
    "triage_attachments",
    "get_chunking_candidates",
    "build_triage_artifact",
]