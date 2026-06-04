"""Pydantic model for the theme-title LLM output.

  - theme_title_generation.yaml -> ThemeTitleResult

Optional with a default so a partial/empty structured response does not raise;
the generator treats a blank title as a warning, not an error.
"""

from __future__ import annotations

from pydantic import BaseModel


class ThemeTitleResult(BaseModel):
    theme_title: str = ""


__all__ = ["ThemeTitleResult"]
