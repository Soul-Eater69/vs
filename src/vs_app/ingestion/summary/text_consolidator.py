"""Summary-mode text consolidation from Jira ticket bodies and attachments."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from vs_app.modules.tickets.text_formatting import (
    clean_text,
    extract_description_text,
)
from vs_app.shared.text_cleaning import clean_extracted_text

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8_000
_MAX_ATTACHMENT_CHARS = 12_000
_MAX_TOTAL_CHARS = 20_000

_DEFAULT_MAX_DOCUMENTS = 4
_DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE = 60_000_000

_SUPPORTED_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}

Progress = Callable[[str], None]


async def consolidate_ticket_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
    progress: Progress | None = None,
) -> str:
    fields: dict = ticket_data.get("fields", {}) or {}
    ticket_key = str(ticket_data.get("key") or "")

    description_text = clean_text(extract_description_text(fields.get("description")))

    api_attachments = _jira_attachments(ticket_data, fields)
    max_docs = int(getattr(cfg, "max_documents", _DEFAULT_MAX_DOCUMENTS) or _DEFAULT_MAX_DOCUMENTS)
    doc_parts = await _resolve_jira_documents(
        api_attachments,
        jira_client,
        cfg,
        budget=max_docs,
        progress=progress,
    )

    parts: list[str] = []
    if description_text:
        parts.append(f"[DESCRIPTION]\n{description_text[:_MAX_DESCRIPTION_CHARS]}")
    parts.extend(doc_parts)

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
    progress: Progress | None = None,
) -> list[str]:
    """Extract text from Jira document attachments up to the document budget."""
    if budget <= 0:
        return []
    all_docs = _document_attachments(api_attachments)
    docs = sorted(all_docs, key=_rank_document_attachment)
    _emit(
        progress,
        f"ATTACHMENTS found={len(api_attachments)} supported_docs={len(all_docs)} max_documents={budget}",
    )

    for att in api_attachments:
        filename = str(att.get("filename") or "attachment")
        if _ext_of(filename) not in _SUPPORTED_EXTENSIONS:
            _emit(progress, f"ATTACHMENT skipped unsupported: {filename}")

    max_size = _max_attachment_size(cfg)
    parts: list[str] = []
    for attempt_idx, att in enumerate(docs, start=1):
        if len(parts) >= budget:
            break
        filename = str(att.get("filename") or "attachment")
        size = int(att.get("size", 0) or 0)
        _emit(progress, f"ATTACHMENT attempting {attempt_idx}/{len(docs)}: {filename} size={size}")
        if size and size > max_size and not _has_pre_extracted_text(att):
            _emit(
                progress,
                f"ATTACHMENT skipped too_large: {filename} size={size} max={max_size}",
            )
            continue
        text = await _materialize_attachment_text(
            att,
            jira_client,
            cfg,
            progress=progress,
        )
        skip, word_count, reason = _should_skip_extracted_text(text, min_words=30)
        if skip:
            _emit(
                progress,
                f"ATTACHMENT skipped weak_text: {filename} reason={reason} words={word_count} chars={len(text or '')}",
            )
            continue
        accepted_idx = len(parts) + 1
        _emit(
            progress,
            f"ATTACHMENT accepted {accepted_idx}/{budget}: {filename} words={word_count} chars={len(text)}",
        )
        parts.append(f"[DOCUMENT: {filename}]\n{text[:_MAX_ATTACHMENT_CHARS]}")
    return parts


def _document_attachments(attachments: list[dict]) -> list[dict]:
    return [a for a in attachments if _ext_of(a.get("filename", "")) in _SUPPORTED_EXTENSIONS]


def _rank_document_attachment(att: dict) -> tuple:
    filename = str(att.get("filename") or "").lower()
    ext = _ext_of(filename)
    primary_name_score = 1 if any(token in filename for token in ("idea", "card")) else 0
    secondary_name_score = 1 if any(
        token in filename for token in ("business case", "proposal", "deck", "initiative")
    ) else 0

    ext_rank = {
        "pptx": 0,
        "pdf": 1,
        "docx": 2,
        "ppt": 3,
        "doc": 4,
    }.get(ext, 9)

    size = int(att.get("size", 0) or 0)
    return (-primary_name_score, -secondary_name_score, ext_rank, size, filename)


async def _materialize_attachment_text(
    att: dict,
    jira_client: Any,
    cfg: Any,
    progress: Progress | None = None,
) -> str:
    filename = str(att.get("filename") or "attachment")
    extracted_text = clean_extracted_text(
        str((att.get("extracted") or {}).get("text") or "")
    )
    if extracted_text:
        _emit(
            progress,
            f"ATTACHMENT using pre-extracted text: {filename} chars={len(extracted_text)}",
        )
        return extracted_text

    try:
        file_bytes = await jira_client.download_attachment(att)
    except Exception as exc:
        _emit(progress, f"ATTACHMENT error downloading: {filename} error={exc}")
        logger.info("Attachment download skipped (%s): %s", att.get("filename"), exc)
        return ""

    try:
        return _extract_bytes_to_text(file_bytes or b"", att, cfg, progress=progress)
    except Exception as exc:
        _emit(progress, f"ATTACHMENT error extracting: {filename} error={exc}")
        logger.warning("Extraction failed for %s: %s", att.get("filename"), exc)
        return ""


def _extract_bytes_to_text(
    file_bytes: bytes,
    att: dict,
    cfg: Any,
    progress: Progress | None = None,
) -> str:
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
        _emit(progress, f"ATTACHMENT error extracting: {att.get('filename')} error={exc}")
        logger.warning("Extraction failed for %s: %s", att.get("filename"), exc)
        return ""

    chunks = result.get("chunks", []) or []
    text = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if chunk.get("text") and not chunk.get("is_boilerplate")
    )
    return clean_extracted_text(text)


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _max_attachment_size(cfg: Any) -> int:
    return int(
        getattr(cfg, "max_prefetch_attachment_size", _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE)
        or _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE
    )


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _should_skip_extracted_text(
    text: str,
    *,
    min_words: int = 30,
) -> tuple[bool, int, str]:
    clean = str(text or "").strip()
    words = _word_count(clean)

    if not clean:
        return True, words, "empty"

    if words < min_words:
        return True, words, f"too_few_words<{min_words}"

    return False, words, "ok"


def _has_pre_extracted_text(att: dict) -> bool:
    return bool(clean_extracted_text(str((att.get("extracted") or {}).get("text") or "")))


def _emit(progress: Progress | None, message: str) -> None:
    if progress:
        progress(message)


__all__ = ["consolidate_ticket_text"]
