"""Pure attachment helpers for the Jira source layer.

No network, no client construction — just text combination and metadata
normalization over already-fetched attachment data.
"""

from __future__ import annotations

from typing import Any


def combine_attachment_texts(texts: list[str], *, separator: str = "\n\n") -> str:
    """Join non-empty texts in order, preserving each block's own formatting."""
    parts = [str(text).strip() for text in texts or [] if text and str(text).strip()]
    return separator.join(parts)


def normalize_attachment_metadata(attachment: Any) -> dict:
    """Normalize a raw attachment object into a compact metadata dict.

    Tolerates Jira-style (``filename``/``mimeType``/``content``) and simpler
    (``name``/``mime_type``/``url``) shapes; unknown shapes yield ``{}``.
    """
    if not isinstance(attachment, dict):
        return {}
    return {
        "id": _text(attachment.get("id")),
        "filename": _text(attachment.get("filename") or attachment.get("name")),
        "mime_type": _text(attachment.get("mimeType") or attachment.get("mime_type")),
        "size": attachment.get("size"),
        "url": _text(attachment.get("content") or attachment.get("url")),
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = ["combine_attachment_texts", "normalize_attachment_metadata"]
