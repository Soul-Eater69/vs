"""
Text consolidation from a Jira ticket.

Responsibility: pull everything from a fetched ticket dict into one
consolidated text block. Chunking is an internal detail — output is a
single string ready for LLM summarization.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from content.formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8_000
_MAX_ATTACHMENT_CHARS = 12_000
_MAX_COMMENT_CHARS = 1_500
_MAX_TOTAL_CHARS = 24_000


async def consolidate_ticket_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> str:
    """
    Fetch and merge all text sources for a ticket into one string.

    Sources (in priority order):
      1. Idea card attachment (PPT/PDF/DOCX) — richest signal
      2. Jira description
      3. Top substantive comments

    Returns a single consolidated string, trimmed to _MAX_TOTAL_CHARS.
    """
    fields: dict = ticket_data.get("fields", {})
    parts: list[str] = []

    # 1. Attachment text
    attachment_text = await _extract_primary_attachment_text(ticket_data, jira_client, cfg)
    if attachment_text:
        parts.append(f"[IDEA CARD]\n{attachment_text[:_MAX_ATTACHMENT_CHARS]}")

    # 2. Description
    raw_desc = fields.get("description")
    desc_text = clean_text(extract_description_text(raw_desc))
    if desc_text:
        parts.append(f"[DESCRIPTION]\n{desc_text[:_MAX_DESCRIPTION_CHARS]}")

    # 3. Substantive comments
    comment_texts = extract_substantive_comments(
        comment_field=fields.get("comment") or {},
        max_comments=3,
    )
    for i, comment in enumerate(comment_texts, start=1):
        parts.append(f"[COMMENT {i}]\n{comment[:_MAX_COMMENT_CHARS]}")

    consolidated = "\n\n".join(parts)
    return consolidated[:_MAX_TOTAL_CHARS]


async def _extract_primary_attachment_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> str:
    """
    Download and extract text from the best candidate attachment.
    Uses the existing routing logic to pick the idea card (PPT/PDF).
    Falls back gracefully if extraction fails.
    """
    attachments = ticket_data.get("attachments", []) or []
    candidate = _pick_best_attachment(attachments)
    if not candidate:
        return ""

    try:
        file_bytes = await jira_client.download_attachment(candidate)
    except Exception as exc:
        logger.warning("Attachment download failed for %s: %s", candidate.get("filename"), exc)
        return ""

    return _extract_bytes_to_text(file_bytes, candidate, cfg)


def _pick_best_attachment(attachments: list[dict]) -> Optional[dict]:
    """Return the most likely idea card attachment (PPT first, then PDF/DOCX)."""
    priority = {"pptx": 0, "ppt": 1, "pdf": 2, "docx": 3, "doc": 4}
    candidates = []
    for att in attachments:
        filename = (att.get("filename") or "").lower()
        ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        if ext in priority:
            candidates.append((priority[ext], att))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _extract_bytes_to_text(file_bytes: bytes, att: dict, cfg: Any) -> str:
    """Extract text from attachment bytes using the appropriate extractor."""
    filename = (att.get("filename") or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

    try:
        if ext in ("pptx", "ppt"):
            from processing.extraction.pptx import extract_pptx
            result = extract_pptx(file_bytes, max_slides=int(getattr(cfg, "max_slides", 60) or 60))
        elif ext == "pdf":
            from processing.extraction.pdf import extract_pdf
            result = extract_pdf(file_bytes, ocr_enabled=False)
        elif ext in ("docx", "doc"):
            from processing.extraction.docx import extract_docx
            result = extract_docx(file_bytes)
        else:
            return ""
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", att.get("filename"), exc)
        return ""

    chunks = result.get("chunks", []) or []
    text = "\n".join(
        c.get("text", "") for c in chunks
        if c.get("text") and not c.get("is_boilerplate")
    )
    return clean_text(text)
