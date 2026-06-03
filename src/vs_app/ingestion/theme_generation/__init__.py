"""Compatibility shim for the old ingestion location of runtime Theme-generation.

Runtime Theme-generation now lives in the first-class package
``vs_app.theme_generation``. This module and its siblings
(``retrieval``, ``generation``, ``orchestrator``, ``search_adapter``) re-export
from the new path so existing imports keep working unchanged.
"""

from __future__ import annotations

from vs_app.theme_generation import (
    ThemeGenerationSearchAdapter,
    extract_matching_theme_refs,
    fetch_theme_examples,
    generate_theme_description,
    generate_theme_for_value_stream,
    generate_themes_for_idea,
    search_idmt_examples,
    select_theme_examples_for_prompt,
)

__all__ = [
    "search_idmt_examples",
    "extract_matching_theme_refs",
    "fetch_theme_examples",
    "select_theme_examples_for_prompt",
    "generate_theme_description",
    "generate_theme_for_value_stream",
    "generate_themes_for_idea",
    "ThemeGenerationSearchAdapter",
]
