"""Runtime stage-generation contract.

Clean, stable data shapes for runtime stage selection. The generator wraps the
existing stage predictor and normalizes its output into these records, so callers
(the theme_generation service / API) depend on this small contract rather than the
predictor's raw payload.

Mirrors the Value Stream generation contract style: internal fields keep natural
shapes (``confidence`` stays 0–1), and ``to_dict`` emits the API-facing public
contract with ``confidence_score`` scaled to 0–100.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class GeneratedStage:
    """One runtime stage selected under a Value Stream."""

    stage_name: str
    value_stream_name: str
    rationale: str
    confidence: float
    stage_id: str = ""
    # "" until the selector/catalog provides direct/implied for stages.
    support_type: str = ""

    @property
    def confidence_score(self) -> int:
        """Internal 0–1 confidence rendered as a 0–100 integer score."""
        return max(0, min(100, round(float(self.confidence or 0.0) * 100)))

    def to_dict(self) -> dict:
        """API-facing public contract for one stage."""
        return {
            "stage_id": self.stage_id,
            "stage_name": self.stage_name,
            "value_stream_name": self.value_stream_name,
            "rationale": self.rationale,
            "confidence_score": self.confidence_score,
            "support_type": self.support_type,
        }


@dataclass(slots=True)
class StageGenerationRequest:
    """Input for runtime stage generation under a single Value Stream."""

    value_stream_name: str
    allowed_stages: list[str] = field(default_factory=list)
    idea_card_text: str | None = None
    value_stream_description: str = ""
    max_output_stages: int | None = None


@dataclass(slots=True)
class StageGenerationResult:
    """Output of runtime stage generation for one Value Stream."""

    value_stream_name: str
    stages: list[GeneratedStage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "value_stream_name": self.value_stream_name,
            "stages": [stage.to_dict() for stage in self.stages],
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


__all__ = [
    "GeneratedStage",
    "StageGenerationRequest",
    "StageGenerationResult",
]
