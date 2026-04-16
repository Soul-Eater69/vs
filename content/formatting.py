"""
Text extraction and normalization helpers used by the ingestion pipeline.

These functions take raw Jira data and produce clean text strings.
No chunking happens here — output is always a single string.
"""

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
        from jira.text.text_processing import extract_adf_text
        return extract_adf_text(raw_description) or ""
    return str(raw_description).strip()


def extract_substantive_comments(comment_field: dict, max_comments: int = 3) -> list[str]:
    """
    Return the top N substantive human comments.
    Strips bot noise and chatter.
    """
    from jira.text.text_processing import (
        extract_comment_texts,
        is_bot_author,
        is_comment_chatter,
    )

    comments_data = comment_field if isinstance(comment_field, dict) else {}
    raw_comments = comments_data.get("comments", []) or []

    result: list[str] = []
    for c in raw_comments:
        if is_bot_author(c):
            continue
        body = c.get("body", "")
        text = _adf_or_str(body)
        if not text or is_comment_chatter(text):
            continue
        result.append(text.strip())
        if len(result) >= max_comments:
            break

    return result


def clean_text(text: str) -> str:
    """Normalize whitespace and remove encoding artifacts."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def _adf_or_str(value: Any) -> str:
    if isinstance(value, (dict, list)):
        from jira.text.text_processing import extract_adf_text
        return extract_adf_text(value) or ""
    return str(value or "").strip()
