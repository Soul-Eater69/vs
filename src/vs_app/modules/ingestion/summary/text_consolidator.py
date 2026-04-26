"""Summary-mode text consolidation from Jira ticket bodies and attachments."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from vs_app.shared.text_cleaning import clean_extracted_text, text_looks_weak
from vs_app.modules.tickets.text_formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8_000
_MAX_ATTACHMENT_CHARS = 12_000
_MAX_COMMENT_CHARS = 1_500
_MAX_TOTAL_CHARS = 24_000

_DEFAULT_MAX_DOCUMENTS = 4
_DEFAULT_MAX_PREFETCH_ATTACHMENTS = 6
_DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE = 60_000_000

_SUPPORTED_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}
_SHAREPOINT_HOSTS = ("sharepoint.com", "sharepoint-df.com", "my.sharepoint")


async def consolidate_ticket_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
    sharepoint_client: Optional[Any] = None,
) -> str:
    fields: dict = ticket_data.get("fields", {}) or {}
    ticket_key = str(ticket_data.get("key") or "")

    description_text = clean_text(extract_description_text(fields.get("description")))
    comment_texts = extract_substantive_comments(
        comment_field=fields.get("comment") or {},
        max_comments=3,
    )

    description_links, api_attachments = _split_attachments(ticket_data, fields)
    max_docs = int(getattr(cfg, "max_documents", _DEFAULT_MAX_DOCUMENTS) or _DEFAULT_MAX_DOCUMENTS)

    sharepoint_parts, unavailable = await _resolve_sharepoint_links(
        description_links, sharepoint_client, cfg, budget=max_docs
    )
    doc_parts = await _resolve_jira_documents(
        api_attachments, jira_client, cfg, budget=max_docs - len(sharepoint_parts)
    )

    parts: list[str] = []
    if description_text:
        parts.append(f"[DESCRIPTION]\n{description_text[:_MAX_DESCRIPTION_CHARS]}")
    # SharePoint content first — most likely to be the primary idea card
    parts.extend(sharepoint_parts)
    parts.extend(doc_parts)
    if unavailable:
        hints = "\n".join(f"- {fn}" for fn in unavailable)
        parts.append(f"[LINKED DOCUMENTS - CONTENT UNAVAILABLE]\n{hints}")
    for idx, comment in enumerate(comment_texts, start=1):
        parts.append(f"[COMMENT {idx}]\n{comment[:_MAX_COMMENT_CHARS]}")

    logger.debug(
        "%s: %d Jira doc(s), %d SharePoint doc(s), %d unavailable link(s)",
        ticket_key or "<unknown>",
        len(doc_parts), len(sharepoint_parts), len(unavailable),
    )
    if not (sharepoint_parts or doc_parts):
        logger.info("No document content resolved for %s; using ticket body only", ticket_key or "<unknown>")

    consolidated = "\n\n".join(p for p in parts if p).strip()
    return consolidated[:_MAX_TOTAL_CHARS]


def _split_attachments(ticket_data: dict, fields: dict) -> tuple[list[dict], list[dict]]:
    """Return (description_links, api_attachments) from the ticket payload."""
    all_atts = list(ticket_data.get("attachments") or [])
    desc_links = list(ticket_data.get("description_attachments") or []) or [
        a for a in all_atts if a.get("is_description_link")
    ]
    api_atts = [a for a in all_atts if not a.get("is_description_link")] or list(
        fields.get("attachment") or []
    )
    return desc_links, api_atts


async def _resolve_sharepoint_links(
    description_links: list[dict],
    sharepoint_client: Optional[Any],
    cfg: Any,
    budget: int,
) -> tuple[list[str], list[str]]:
    """Fetch SharePoint links (up to budget) in parallel. Returns (parts, unavailable_filenames)."""
    urls = [
        u for u in (str(lnk.get("url") or lnk.get("content") or "") for lnk in description_links)
        if u and _is_sharepoint(u)
    ][:max(0, budget)]
    if not urls:
        return [], []

    if sharepoint_client is not None:
        fetched = await asyncio.gather(
            *[asyncio.to_thread(_fetch_sharepoint_text, u, sharepoint_client, cfg) for u in urls]
        )
    else:
        fetched = [(_filename_from_url(u), "") for u in urls]

    parts: list[str] = []
    unavailable: list[str] = []
    for filename, text in fetched:
        ext = _ext_of(filename)
        if ext not in _SUPPORTED_EXTENSIONS:
            continue
        if text and not text_looks_weak(text, min_words=30):
            parts.append(f"[LINKED DOCUMENT: {filename}]\n{text[:_MAX_ATTACHMENT_CHARS]}")
        elif filename:
            unavailable.append(filename)
    return parts, unavailable


async def _resolve_jira_documents(
    api_attachments: list[dict],
    jira_client: Any,
    cfg: Any,
    budget: int,
) -> list[str]:
    """Extract text from Jira document attachments up to the remaining budget."""
    if budget <= 0:
        return []
    prefetched = await _prefetch_attachment_bytes(jira_client, api_attachments, cfg)
    parts: list[str] = []
    for att in _document_attachments(api_attachments):
        if len(parts) >= budget:
            break
        text = await _materialize_attachment_text(att, prefetched, jira_client, cfg)
        if not text or text_looks_weak(text, min_words=30):
            continue
        filename = str(att.get("filename") or "attachment")
        parts.append(f"[DOCUMENT: {filename}]\n{text[:_MAX_ATTACHMENT_CHARS]}")
    return parts


# ---------------------------------------------------------------------------
# SharePoint helpers
# ---------------------------------------------------------------------------

def _fetch_sharepoint_text(url: str, sharepoint_client: Any, cfg: Any) -> tuple[str, str]:
    """Sync: resolve URL → download → extract. Returns (filename, text)."""
    try:
        item = sharepoint_client.resolve_browser_url(url)
        if not item:
            return _filename_from_url(url), ""
        filename = str(item.get("name") or "")
        if _ext_of(filename) not in _SUPPORTED_EXTENSIONS:
            return filename, ""
        download_url = str(item.get("@microsoft.graph.downloadUrl") or "")
        if not download_url:
            return filename, ""
        file_bytes = sharepoint_client.get_file_content_by_url(download_url)
        if not file_bytes:
            return filename, ""
        return filename, _extract_bytes_to_text(file_bytes, {"filename": filename}, cfg)
    except Exception as exc:
        logger.warning("SharePoint fetch failed for %s: %s", url[:80], exc)
        return _filename_from_url(url), ""


def _is_sharepoint(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(sp in host for sp in _SHAREPOINT_HOSTS)


def _filename_from_url(url: str) -> str:
    """Extract filename from URL path; returns empty string for opaque sharing tokens."""
    segment = unquote(urlparse(url).path).rsplit("/", 1)[-1]
    return segment if "." in segment else ""


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------

def _document_attachments(attachments: list[dict]) -> list[dict]:
    return [a for a in attachments if _ext_of(a.get("filename", "")) in _SUPPORTED_EXTENSIONS]


async def _prefetch_attachment_bytes(
    jira_client: Any,
    attachments: list[dict],
    cfg: Any,
) -> dict[str, bytes]:
    max_prefetch = int(
        getattr(cfg, "max_prefetch_attachments", _DEFAULT_MAX_PREFETCH_ATTACHMENTS)
        or _DEFAULT_MAX_PREFETCH_ATTACHMENTS
    )
    max_size = int(
        getattr(cfg, "max_prefetch_attachment_size", _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE)
        or _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE
    )
    prefetched: dict[str, bytes] = {}
    for att in attachments:
        if len(prefetched) >= max_prefetch:
            break
        if _ext_of(att.get("filename", "")) not in _SUPPORTED_EXTENSIONS:
            continue
        size = int(att.get("size", 0) or 0)
        if size and size > max_size:
            continue
        att_id = _attachment_key(att)
        if not att_id or att_id in prefetched:
            continue
        try:
            file_bytes = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment prefetch skipped (%s): %s", att.get("filename"), exc)
            continue
        if file_bytes:
            prefetched[att_id] = file_bytes
    return prefetched


async def _materialize_attachment_text(
    att: dict,
    prefetched: dict[str, bytes],
    jira_client: Any,
    cfg: Any,
) -> str:
    extracted_text = clean_extracted_text(
        str((att.get("extracted") or {}).get("text") or "")
    )
    if extracted_text:
        return extracted_text

    file_bytes = prefetched.get(_attachment_key(att))
    if file_bytes is None:
        try:
            file_bytes = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment download skipped (%s): %s", att.get("filename"), exc)
            return ""

    return _extract_bytes_to_text(file_bytes or b"", att, cfg)


def _extract_bytes_to_text(file_bytes: bytes, att: dict, cfg: Any) -> str:
    if not file_bytes:
        return ""
    filename = (att.get("filename") or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    try:
        if ext in ("pptx", "ppt"):
            from vs_app.integrations.files.pptx_extractor import extract_pptx
            result = extract_pptx(
                file_bytes,
                max_slides=int(getattr(cfg, "max_slides", 60) or 60),
            )
        elif ext == "pdf":
            from vs_app.integrations.files.pdf_extractor import extract_pdf
            result = extract_pdf(
                file_bytes,
                ocr_enabled=bool(getattr(cfg, "ocr_enabled", False)),
                max_pages=int(getattr(cfg, "max_pages", 60) or 60),
            )
        elif ext in ("docx", "doc"):
            from vs_app.integrations.files.docx_extractor import extract_docx
            result = extract_docx(file_bytes)
        else:
            return ""
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", att.get("filename"), exc)
        return ""

    chunks = result.get("chunks", []) or []
    text = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if chunk.get("text") and not chunk.get("is_boilerplate")
    )
    return clean_extracted_text(text)


def _attachment_key(att: dict) -> str:
    return str(att.get("id") or att.get("attachment_id") or att.get("filename") or "")


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


__all__ = ["consolidate_ticket_text"]
