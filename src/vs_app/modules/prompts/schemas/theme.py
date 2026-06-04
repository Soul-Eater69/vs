"""Pydantic model for theme-generation LLM output.

  - theme_generation.yaml → ThemeGenerationResult

Stages are intentionally NOT part of this schema: stage selection stays with
``predict_value_stream_stages`` and the allowed dropdown/catalog. Fields are
optional with lenient defaults so a partial/empty gateway response does not raise.
"""

from __future__ import annotations

from pydantic import BaseModel


class ThemeGenerationResult(BaseModel):
    """Output of the legacy combined theme_generation prompt (description + needs).

    Retained for backward compatibility. Runtime generation now uses the split
    ``ThemeDescriptionResult`` and ``BusinessNeedsResult`` below.
    """

    theme_description: str = ""
    business_needs: str = ""


class ThemeDescriptionResult(BaseModel):
    """Output of theme_description_generation.yaml."""

    theme_description: str = ""


class BusinessNeedsResult(BaseModel):
    """Output of business_needs_generation.yaml."""

    business_needs: str = ""


__all__ = [
    "ThemeGenerationResult",
    "ThemeDescriptionResult",
    "BusinessNeedsResult",
]
