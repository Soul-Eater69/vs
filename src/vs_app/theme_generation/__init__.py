"""Runtime Theme-generation components.

This package is used by manual/API-facing generation flows:
- retrieval of historic examples
- theme description/business-needs generation
- orchestration with Value Stream + stage selection

Ingestion/index/export code remains under vs_app.ingestion.
"""

from __future__ import annotations

from vs_app.theme_generation.descriptions import generate_theme_description
from vs_app.theme_generation.orchestrator import (
    generate_theme_for_value_stream,
    generate_themes_for_idea,
)
from vs_app.theme_generation.retrieval import (
    extract_matching_theme_refs,
    fetch_theme_examples,
    search_idmt_examples,
    select_theme_examples_for_prompt,
)
from vs_app.theme_generation.search_adapter import ThemeGenerationSearchAdapter

__all__ = [
    "search_idmt_examples",
    "extract_matching_theme_refs",
    "fetch_theme_examples",
    "select_theme_examples_for_prompt",
    "generate_theme_description",
    "ThemeGenerationSearchAdapter",
    "generate_theme_for_value_stream",
    "generate_themes_for_idea",
]
