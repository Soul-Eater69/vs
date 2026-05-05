"""Jira attachment-specific helper re-exports."""

from __future__ import annotations

from vs_app.integrations.files.attachment_extraction import (  # noqa: F401
    download_attachment,
    fetch_attachment_content,
)

__all__ = [
    "download_attachment",
    "fetch_attachment_content",
]
