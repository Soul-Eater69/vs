"""Runtime Theme-generation support (retrieval + generation).

Separate from ``index_documents`` (which shapes/builds docs) and ``upload``
(which manages the index). Feature 14A adds retrieval helpers only; LLM prompt
assembly and generation arrive in Feature 14B.
"""

from __future__ import annotations

from vs_app.ingestion.theme_generation.generation import generate_theme_description
from vs_app.ingestion.theme_generation.retrieval import (
    extract_matching_theme_refs,
    fetch_theme_examples,
    search_idmt_examples,
    select_theme_examples_for_prompt,
)

__all__ = [
    "search_idmt_examples",
    "extract_matching_theme_refs",
    "fetch_theme_examples",
    "select_theme_examples_for_prompt",
    "generate_theme_description",
]
