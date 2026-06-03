"""Runtime Value Stream generation contract.

Clean, stable data shapes for runtime Value Stream generation. The generator
wraps the existing RAG pipeline and normalizes its rich payload into these
records, so callers (the theme_generation service / API) depend on this small
contract rather than the sprawling RAG result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Support strength of a generated Value Stream, derived deterministically from
# existing RAG candidate metadata (no extra LLM pass). See validators.derive_support_type.
SupportType = str  # "direct" | "implied"


@dataclass(slots=True)
class GeneratedValueStream:
    """One runtime Value Stream candidate for a new IDMT request."""

    name: str
    entity_id: str
    support_type: SupportType
    confidence: float
    rationale: str
    evidence: list[str] = field(default_factory=list)
    historic_idmt_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entity_id": self.entity_id,
            "support_type": self.support_type,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "historic_idmt_ids": list(self.historic_idmt_ids),
        }


@dataclass(slots=True)
class ValueStreamGenerationRequest:
    """Input for runtime Value Stream generation.

    ``custom_instruction`` is accepted for forward compatibility but is not yet
    applied to the prompt in this phase; supplying it emits a warning.
    """

    idea_card_text: str | None = None
    ticket_id: str | None = None
    top_n: int = 10
    custom_instruction: str | None = None


@dataclass(slots=True)
class ValueStreamGenerationResult:
    """Output of runtime Value Stream generation."""

    value_streams: list[GeneratedValueStream] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "value_streams": [vs.to_dict() for vs in self.value_streams],
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


__all__ = [
    "SupportType",
    "GeneratedValueStream",
    "ValueStreamGenerationRequest",
    "ValueStreamGenerationResult",
]
