"""Small helpers shared across Jira value-stream extraction modules."""

from __future__ import annotations

import re
from typing import Any

_VS_PREFIX_RE = re.compile(
    r"^(?:(?:GROUP-\d+(?:,\s*|\s*(?:&|and)\s*|\s+)*)+|THEME\s*#?\s*\d+)(?:\s*\([^)]+\))?\s*:\s*",
    re.IGNORECASE,
)
_VS_SUFFIX_RE = re.compile(
    r"\s*-\s*(?:IVL(?:\s|)[A-Z]+-\d+|APP\d{5,}|P\d{5,}).*$",
    re.IGNORECASE,
)


def clean_value_stream_name(summary: str) -> str:
    """Normalize linked theme/value-stream summaries to just the theme name."""
    text = (summary or "").strip()
    if not text:
        return ""
    text = _VS_PREFIX_RE.sub("", text)
    text = _VS_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -:")


def str_field(d: dict[str, Any], key: str) -> str:
    """Safely extract a string field from a dict, stripping whitespace."""
    return str(d.get(key) or "").strip()


def nested_str_field(d: dict[str, Any], outer: str, inner: str) -> str:
    """Safely extract d[outer][inner] as a stripped string."""
    sub = d.get(outer)
    if isinstance(sub, dict):
        return str(sub.get(inner) or "").strip()
    return ""
