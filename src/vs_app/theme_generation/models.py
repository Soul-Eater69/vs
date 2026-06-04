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
class ThemeGenerationRequest:
    """Input for runtime theme composition for one IDMT request."""

    idea_card_text: str | None = None
    ticket_id: str | None = None
    top_n_value_streams: int = 10
    custom_instruction: str | None = None


@dataclass(slots=True)
class GeneratedTheme:
    """One Theme: a selected Value Stream with its stages and generated text."""

    value_stream: GeneratedValueStream
    stages: list[GeneratedStage] = field(default_factory=list)
    theme_description: str = ""
    business_needs: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "value_stream": self.value_stream.to_dict(),
            "stages": [stage.to_dict() for stage in self.stages],
            "theme_description": self.theme_description,
            "business_needs": self.business_needs,
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
    "ThemeGenerationRequest",
    "GeneratedTheme",
    "ThemeGenerationResult",
]
