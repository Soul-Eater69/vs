"""General ticket text normalization helpers used by ingestion."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def extract_description_text(raw_description: Any) -> str:
    """Convert Jira description (ADF dict or plain string) to clean text."""
    if not raw_description:
        return ""
    if isinstance(raw_description, (dict, list)):
        from vs_app.modules.tickets.text_processing import extract_adf_text

        return extract_adf_text(raw_description) or ""
    return str(raw_description).strip()


def extract_substantive_comments(comment_field: dict, max_comments: int = 3) -> list[str]:
    """Return the top N substantive human comments, skipping bot noise."""
    from vs_app.modules.tickets.text_processing import extract_comment_texts

    comments_data = comment_field if isinstance(comment_field, dict) else {}
    return extract_comment_texts(comments_data, max_comments=max_comments)


def clean_text(text: str) -> str:
    """Normalize whitespace and remove encoding artifacts."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def adf_or_str(value: Any) -> str:
    if isinstance(value, (dict, list)):
        from vs_app.modules.tickets.text_processing import extract_adf_text

        return extract_adf_text(value) or ""
    return str(value or "").strip()


__all__ = [
    "adf_or_str",
    "clean_text",
    "extract_description_text",
    "extract_substantive_comments",
]
