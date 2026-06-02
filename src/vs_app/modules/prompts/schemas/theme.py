"""Pydantic model for theme-generation LLM output.

  - theme_generation.yaml → ThemeGenerationResult

Stages are intentionally NOT part of this schema: stage selection stays with
``predict_value_stream_stages`` and the allowed dropdown/catalog. Fields are
optional with lenient defaults so a partial/empty gateway response does not raise.
"""

from __future__ import annotations

from pydantic import BaseModel


class ThemeGenerationResult(BaseModel):
    """Output of the theme_generation prompt (description + business needs)."""

    theme_description: str = ""
    business_needs: str = ""


__all__ = ["ThemeGenerationResult"]
