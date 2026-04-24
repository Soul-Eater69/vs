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
# Product/release prefix like "APR 2.0 " or "CP 2024.5 " — 2-5 uppercase
# letters followed by a dotted version number. Stripped so BM25 can match
# against canonical names like "Order to Cash" without version noise.
_VS_PRODUCT_PREFIX_RE = re.compile(r"^[A-Z]{2,5}\s+\d+(?:\.\d+)*\s+(?=\S)")
# Separator between product/release prefix and VS name. Dashes (`-`, `–`, `—`)
# must be space-padded on both sides to avoid breaking hyphenated names like
# "Request-Inquiry". Colons only need a trailing space, since Jira writes
# "Enhancements: Discover Business Insight" without a leading space.
_VS_SEPARATOR_RE = re.compile(r"\s[-–—]\s+|:\s+")


def clean_value_stream_name(summary: str) -> str:
    """Normalize linked theme/value-stream summaries to just the theme name."""
    text = (summary or "").strip()
    if not text:
        return ""
    text = _VS_PREFIX_RE.sub("", text)
    text = _VS_PRODUCT_PREFIX_RE.sub("", text)
    text = _VS_SUFFIX_RE.sub("", text)
    matches = list(_VS_SEPARATOR_RE.finditer(text))
    if matches:
        tail = text[matches[-1].end():].strip()
        if len(tail) >= 4:
            text = tail
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
