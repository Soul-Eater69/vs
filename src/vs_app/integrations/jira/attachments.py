"""Jira attachment-specific helpers.

Generic attachment download (with SharePoint fallback) and extraction live in
`vs_app.integrations.files`. This module re-exports them for convenience when
callers want a single Jira-namespaced import surface; no Jira-specific
URL/auth behavior lives here today.
"""

from __future__ import annotations

from vs_app.integrations.files.attachment_extraction import (  # noqa: F401
    download_attachment,
    fetch_attachment_content,
)
from vs_app.integrations.files.description_links import (  # noqa: F401
    extract_description_link_attachments,
)

__all__ = [
    "download_attachment",
    "extract_description_link_attachments",
    "fetch_attachment_content",
]
