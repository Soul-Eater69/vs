from __future__ import annotations

from dataclasses import dataclass

# Support classifications shared by Value Stream and stage rows. ``unknown`` is
# used when a stage/value stream is known from Jira GT but the original ticket
# context has not yet been classified as supporting it.
SUPPORT_TYPES = ("direct", "implied", "weak_broad", "not_in_context", "unknown")


@dataclass
class TicketContext:
    ticket_id: str
    summary: str = ""
    description: str = ""
    idea_card_text: str = ""
    attachment_text: str = ""
    extracted_text: str = ""
    generated_summary: str = ""
    retrieval_text: str = ""


@dataclass
class ValueStreamSupport:
    value_stream_name: str
    value_stream_id: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""
    source: str = ""
    confidence: float | None = None


@dataclass
class StageSupport:
    value_stream_name: str
    stage_name: str
    value_stream_id: str = ""
    stage_id: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""
    source: str = ""
    confidence: float | None = None


__all__ = [
    "SUPPORT_TYPES",
    "TicketContext",
    "ValueStreamSupport",
    "StageSupport",
]
