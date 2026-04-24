"""Lightweight text assembly for historical ticket extraction."""

from __future__ import annotations

from typing import Optional

from .text_processing import clean_description, extract_adf_text, extract_comment_texts


# ---------------------------------------------------------------------------
# Retrieval text assembly
# ---------------------------------------------------------------------------

_MAX_RETRIEVAL_LEN = 6000


def build_retrieval_text(
    title: str,
    description_cleaned: str,
    attachment_texts: Optional[list[str]] = None,
    comment_texts: Optional[list[str]] = None,
) -> str:
    """Assemble a single retrieval string from ticket components.

    Concatenates title, description, attachment texts, and comments
    with a hard cap to keep the enrichment prompt manageable.
    """
    parts: list[str] = []

    if title:
        parts.append(title)

    if description_cleaned:
        parts.append(description_cleaned[:2000])

    for att_text in (attachment_texts or [])[:3]:
        if att_text and att_text.strip():
            parts.append(att_text[:1500])

    for comment in (comment_texts or [])[:3]:
        if comment and comment.strip():
            parts.append(comment[:500])

    combined = "\n\n".join(p.strip() for p in parts if p.strip())
    return combined[:_MAX_RETRIEVAL_LEN]


__all__ = [
    "build_retrieval_text",
    "clean_description",
    "extract_adf_text",
    "extract_comment_texts",
]
