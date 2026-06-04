"""Runtime theme composition contract.

The theme_generation service composes the runtime pieces — Value Stream
generation, stage generation, and theme description / business-needs — into one
Theme per selected Value Stream. These records nest the already-agreed Value
Stream and stage public contracts; titles / L2 / L3 are intentionally not part of
this phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vs_app.stage_generation.models import GeneratedStage
from vs_app.value_stream_generation.models import GeneratedValueStream


@dataclass(slots=True)
class GeneratedL2Capability:
    """One L2 (mid-level) business capability for a theme."""

    capability_name: str
    rationale: str
    confidence: float
    capability_id: str = ""

    @property
    def confidence_score(self) -> int:
        return max(0, min(100, round(float(self.confidence or 0.0) * 100)))

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "rationale": self.rationale,
            "confidence_score": self.confidence_score,
        }


@dataclass(slots=True)
class GeneratedL3Capability:
    """One L3 (sub-) business capability rolling up to an L2 capability."""

    capability_name: str
    parent_l2_capability_name: str
    rationale: str
    confidence: float
    capability_id: str = ""

    @property
    def confidence_score(self) -> int:
        return max(0, min(100, round(float(self.confidence or 0.0) * 100)))

    def to_dict(self) -> dict:
        return {
            "capability_id": self.capability_id,
            "capability_name": self.capability_name,
            "parent_l2_capability_name": self.parent_l2_capability_name,
            "rationale": self.rationale,
            "confidence_score": self.confidence_score,
        }


@dataclass(slots=True)
class ThemeGenerationRequest:
    """Input for runtime theme composition for one IDMT request."""

    idea_card_text: str | None = None
    ticket_id: str | None = None
    idmt_title: str = ""
    # Summary-only context for stage prediction (ticket/generated summary). Stage
    # generation uses this, never the idea card body or description.
    generated_summary: str = ""
    top_n_value_streams: int = 10
    custom_instruction: str | None = None


@dataclass(slots=True)
class GeneratedTheme:
    """One Theme: a selected Value Stream with its stages and generated text."""

    value_stream: GeneratedValueStream
    stages: list[GeneratedStage] = field(default_factory=list)
    theme_title: str = ""
    theme_description: str = ""
    business_needs: str = ""
    l2_capabilities: list[GeneratedL2Capability] = field(default_factory=list)
    l3_capabilities: list[GeneratedL3Capability] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "theme_title": self.theme_title,
            "value_stream": self.value_stream.to_dict(),
            "stages": [stage.to_dict() for stage in self.stages],
            "theme_description": self.theme_description,
            "business_needs": self.business_needs,
            "l2_capabilities": [cap.to_dict() for cap in self.l2_capabilities],
            "l3_capabilities": [cap.to_dict() for cap in self.l3_capabilities],
        }


@dataclass(slots=True)
class ThemeGenerationResult:
    """Output of runtime theme composition: one Theme per selected Value Stream."""

    themes: list[GeneratedTheme] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "themes": [theme.to_dict() for theme in self.themes],
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


__all__ = [
    "GeneratedL2Capability",
    "GeneratedL3Capability",
    "ThemeGenerationRequest",
    "GeneratedTheme",
    "ThemeGenerationResult",
]
