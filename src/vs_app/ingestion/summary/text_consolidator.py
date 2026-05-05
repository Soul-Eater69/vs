"""Summary-mode text consolidation from Jira ticket bodies and attachments."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from vs_app.modules.tickets.text_formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)
from vs_app.shared.text_cleaning import clean_extracted_text, text_looks_weak

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8_000
_MAX_ATTACHMENT_CHARS = 12_000
_MAX_COMMENT_CHARS = 1_500
_MAX_TOTAL_CHARS = 20_000

_DEFAULT_MAX_DOCUMENTS = 4
_DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE = 60_000_000

_SUPPORTED_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}


async def consolidate_ticket_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> str:
    fields: dict = ticket_data.get("fields", {}) or {}
    ticket_key = str(ticket_data.get("key") or "")

    description_text = clean_text(extract_description_text(fields.get("description")))
    comment_texts = extract_substantive_comments(
        comment_field=fields.get("comment") or {},
        max_comments=3,
    )

    api_attachments = _jira_attachments(ticket_data, fields)
    max_docs = int(getattr(cfg, "max_documents", _DEFAULT_MAX_DOCUMENTS) or _DEFAULT_MAX_DOCUMENTS)
    doc_parts = await _resolve_jira_documents(api_attachments, jira_client, cfg, budget=max_docs)

    parts: list[str] = []
    if description_text:
        parts.append(f"[DESCRIPTION]\n{description_text[:_MAX_DESCRIPTION_CHARS]}")
    parts.extend(doc_parts)
    for idx, comment in enumerate(comment_texts, start=1):
        parts.append(f"[COMMENT {idx}]\n{comment[:_MAX_COMMENT_CHARS]}")

    logger.debug("%s: %d Jira doc(s)", ticket_key or "<unknown>", len(doc_parts))
    if not doc_parts:
        logger.info("No document content resolved for %s; using ticket body only", ticket_key or "<unknown>")

    consolidated = "\n\n".join(p for p in parts if p).strip()
    return consolidated[:_MAX_TOTAL_CHARS]


def _jira_attachments(ticket_data: dict, fields: dict) -> list[dict]:
    return list(ticket_data.get("attachments") or []) or list(fields.get("attachment") or [])


async def _resolve_jira_documents(
    api_attachments: list[dict],
    jira_client: Any,
    cfg: Any,
    budget: int,
) -> list[str]:
    """Extract text from Jira document attachments up to the document budget."""
    if budget <= 0:
        return []
    docs = sorted(_document_attachments(api_attachments), key=_rank_document_attachment)
    docs = docs[:budget]
    prefetched = await _prefetch_attachment_bytes(jira_client, docs, cfg)
    parts: list[str] = []
    for att in docs:
        text = await _materialize_attachment_text(att, prefetched, jira_client, cfg)
        if not text or text_looks_weak(text, min_words=30):
            continue
        filename = str(att.get("filename") or "attachment")
        parts.append(f"[DOCUMENT: {filename}]\n{text[:_MAX_ATTACHMENT_CHARS]}")
    return parts


def _document_attachments(attachments: list[dict]) -> list[dict]:
    return [a for a in attachments if _ext_of(a.get("filename", "")) in _SUPPORTED_EXTENSIONS]


def _rank_document_attachment(att: dict) -> tuple:
    filename = str(att.get("filename") or "").lower()
    ext = _ext_of(filename)
    name_score = 0
    if any(
        token in filename
        for token in ("idea", "card", "business case", "proposal", "deck", "initiative")
    ):
        name_score = 1

    ext_rank = {
        "pptx": 0,
        "ppt": 1,
        "pdf": 2,
        "docx": 3,
        "doc": 4,
    }.get(ext, 9)

    size = int(att.get("size", 0) or 0)
    return (-name_score, ext_rank, size, filename)


async def _prefetch_attachment_bytes(
    jira_client: Any,
    attachments: list[dict],
    cfg: Any,
) -> dict[str, bytes]:
    max_size = int(
        getattr(cfg, "max_prefetch_attachment_size", _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE)
        or _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE
    )
    prefetched: dict[str, bytes] = {}
    for att in attachments:
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
