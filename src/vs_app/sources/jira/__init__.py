"""Reusable Jira source extraction package.

This package fetches IDMT, Theme/GROUP, Epic, idea-card, and attachment data for
both batch ingestion and runtime generation flows. Extraction runs over an
injected, duck-typed client; this package never constructs a live client.
"""

from __future__ import annotations

from vs_app.sources.jira.extractor import extract_idmt_record
from vs_app.sources.jira.models import (
    ExtractedEpicRecord,
    ExtractedIDMTRecord,
    ExtractedThemeRecord,
)

__all__ = [
    "ExtractedIDMTRecord",
    "ExtractedThemeRecord",
    "ExtractedEpicRecord",
    "extract_idmt_record",
]
