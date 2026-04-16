"""Extract attachment-like links embedded in Jira description text."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict, List, Tuple
from urllib.parse import unquote, urlparse

from jira.attachments.constants import (
    DESC_BARE_FILE_URL_RE,
    DESC_MD_LINK_RE,
    DESC_WIKI_LINK_RE,
    EXT_TO_MIME,
)


def extract_description_link_attachments(description: Any) -> List[Dict[str, Any]]:
    """Parse wiki-links, markdown links, and bare file URLs from a description string.

    Returns a list of synthetic attachment dicts compatible with the Jira attachment schema.
    """
    if not isinstance(description, str) or not description.strip():
        return []

    candidates: List[Tuple[str, str]] = []
    for pattern in (DESC_WIKI_LINK_RE, DESC_MD_LINK_RE):
        for match in pattern.finditer(description):
            label = str(match.group("label") or "").strip()
            url = str(match.group("url") or "").strip()
            if url:
                candidates.append((label, url))

    for match in DESC_BARE_FILE_URL_RE.finditer(description):
        url = str(match.group("url") or "").strip()
        if url:
            candidates.append(("", url))

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for label, url in candidates:
        url_key = url.lower()
        if url_key in seen:
            continue
        seen.add(url_key)

        filename = _infer_filename(label, url)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime_type = EXT_TO_MIME.get(ext, "application/octet-stream")
        synthetic_id = f"desc-link-{sha256(url.encode('utf-8')).hexdigest()[:12]}"

        out.append({
            "id": synthetic_id,
            "filename": filename,
            "mimeType": mime_type,
            "content": url,
            "size": 0,
            "created": "",
            "source": "description_link",
            "is_description_link": True,
            "display_text": label or filename,
        })

    return out


def _infer_filename(label: str, url: str) -> str:
    """Best-effort filename from a link label or URL path."""
    label = (label or "").strip()
    if "." in label and len(label.rsplit(".", 1)[-1]) <= 5:
        return label

    parsed = urlparse(url)
    basename = unquote(parsed.path.rsplit("/", 1)[-1]) if parsed.path else ""
    if basename:
        return basename

    return label or "description link attachment"
