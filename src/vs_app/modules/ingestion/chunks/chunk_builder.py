"""Chunk-building helpers used by the canonical chunks pipeline."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ingestion.adapters.llm import complete_text
from ingestion.domain.tickets.documents import ChunkDocument
from vs_app.modules.tickets.text_formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)

from ..attachments.materializer import materialize_attachment_documents
from ..attachments.roles import doc_role_weight, normalize_doc_role
from ..attachments.router import get_routing_candidates, route_attachments

logger = logging.getLogger(__name__)

_BODY_PARAGRAPH_MIN_WORDS = 15
_MAX_BODY_PARAGRAPHS = 40
_PREFETCHABLE_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}


async def build_from_attachments(
    *,
    ticket_key: str,
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> tuple[list[ChunkDocument], list[ChunkDocument]]:
    attachments = list(ticket_data.get("attachments", []) or [])
    if not attachments:
        return [], []

    fields = ticket_data.get("fields", {}) or {}
    ticket_summary = str(fields.get("summary") or ticket_key)

    seed_primary, _seed_supporting, _seed_quality, seed_artifact = route_attachments(
        attachments=attachments,
        ticket_summary=ticket_summary,
        download_fn=None,
    )
    prefetch_targets = unique_attachments(
        ([seed_primary] if seed_primary else [])
        + get_routing_candidates(seed_artifact)
        + attachments
    )
    prefetched = await prefetch_attachment_bytes(jira_client, prefetch_targets, cfg)
    if not prefetched:
        return [], []

    def cached_download(att: dict) -> bytes:
        att_id = attachment_key(att)
        file_bytes = prefetched.get(att_id)
        if file_bytes is None:
            raise RuntimeError(f"Not prefetched: {att.get('filename')}")
        return file_bytes

    primary, _supporting, _quality, routing_artifact = route_attachments(
        attachments=attachments,
        ticket_summary=ticket_summary,
        download_fn=cached_download,
    )
    chunk_candidates = get_routing_candidates(routing_artifact)
    ordered_candidates = unique_attachments(([primary] if primary else []) + chunk_candidates)

    processed_docs: list[dict[str, Any]] = []
    max_docs = int(getattr(cfg, "max_chunk_attachments", 6) or 6)
    for att in ordered_candidates[:max_docs]:
        att_id = attachment_key(att)
        file_bytes = prefetched.get(att_id)
        if file_bytes is None:
            try:
                file_bytes = await jira_client.download_attachment(att)
            except Exception as exc:
                logger.info("Attachment download skipped (%s): %s", att.get("filename"), exc)
                continue

        leaves = extract_attachment_leaves(file_bytes or b"", att, cfg)
        leaves = [leaf for leaf in leaves if leaf.get("text") and not leaf.get("is_boilerplate")]
        if not leaves:
            continue
        processed_docs.append(
            {
                "attachment_id": att_id,
                "attachment_name": str(att.get("filename") or att_id),
                "attachment_type": str(att.get("ext") or ext_of(att.get("filename", ""))),
                "source_url": str(att.get("content") or att.get("url") or ticket_key),
                "doc_role": normalize_doc_role(att),
                "leaves": leaves,
            }
        )

    if not processed_docs:
        return [], []

    return materialize_attachment_documents(ticket_key, processed_docs)


async def prefetch_attachment_bytes(
    jira_client: Any,
    attachments: list[dict],
    cfg: Any,
) -> dict[str, bytes]:
    max_prefetch = int(getattr(cfg, "max_prefetch_attachments", 8) or 8)
    max_size = int(getattr(cfg, "max_prefetch_attachment_size", 60_000_000) or 60_000_000)

    prefetched: dict[str, bytes] = {}
    for att in attachments:
        if len(prefetched) >= max_prefetch:
            break
        ext = ext_of(att.get("filename", ""))
        size = int(att.get("size", 0) or 0)
        if ext not in _PREFETCHABLE_EXTENSIONS:
            continue
        if size and size > max_size:
            continue

        att_id = attachment_key(att)
        if att_id in prefetched:
            continue
        try:
            prefetched[att_id] = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment prefetch skipped (%s): %s", att.get("filename"), exc)

    return prefetched


def extract_attachment_leaves(file_bytes: bytes, attachment: dict, cfg: Any) -> list[dict]:
    ext = str(attachment.get("ext") or ext_of(attachment.get("filename", ""))).lower()
    doc_role = normalize_doc_role(attachment)
    weight = doc_role_weight(doc_role)

    extracted = attachment.get("extracted") or {}
    try:
        if not extracted.get("chunks"):
            if ext in ("pptx", "ppt"):
                from vs_app.integrations.files.pptx_extractor import extract_pptx

                extracted = extract_pptx(
                    file_bytes,
                    max_slides=int(getattr(cfg, "max_slides", 60) or 60),
                )
            elif ext == "pdf":
                from vs_app.integrations.files.pdf_extractor import extract_pdf

                extracted = extract_pdf(
                    file_bytes,
                    ocr_enabled=bool(getattr(cfg, "ocr_enabled", False)),
                    max_pages=int(getattr(cfg, "max_pages", 60) or 60),
                )
            elif ext in ("docx", "doc"):
                from vs_app.integrations.files.docx_extractor import extract_docx

                extracted = extract_docx(file_bytes)
            else:
                return []
    except Exception as exc:
        logger.warning("Attachment extraction failed (%s): %s", attachment.get("filename"), exc)
        return []

    leaves: list[dict] = []
    for idx, raw in enumerate(extracted.get("chunks", []) or []):
        text = clean_text(str(raw.get("text") or ""))
        if not text:
            continue
        section_title = str(
            raw.get("section_title")
            or raw.get("slide_title")
            or attachment.get("filename")
            or ""
        ).strip()
        slide_num = raw.get("slide_num")
        page_num = raw.get("page_num")
        page_range = raw.get("page_range")
        slide_range = raw.get("slide_range")
        source = str(raw.get("source") or source_for_ext(ext))
        header_hierarchy = build_header_hierarchy(
            section_title,
            slide_num,
            page_num,
            page_range,
            slide_range,
        )

        leaves.append(
            {
                "chunk_id": str(raw.get("chunk_id") or f"{attachment_key(attachment)}-{idx}"),
                "text": text,
                "source": source,
                "section_title": section_title,
                "slide_title": str(raw.get("slide_title") or ""),
                "slide_num": slide_num,
                "page_num": page_num,
                "page_range": page_range,
                "slide_range": slide_range,
                "is_boilerplate": bool(raw.get("is_boilerplate", False)),
                "weight_multiplier": float(raw.get("weight_multiplier", weight) or weight),
                "extraction_confidence": float(raw.get("extraction_confidence", 0.9) or 0.9),
                "word_count": int(raw.get("word_count") or len(text.split())),
                "attachment_id": attachment_key(attachment),
                "attachment_name": str(attachment.get("filename") or ""),
                "attachment_type": ext,
                "header_hierarchy": header_hierarchy,
            }
        )

    return leaves


def build_from_ticket_body(
    ticket_key: str,
    ticket_data: dict,
    llm_client: Optional[Any],
    cfg: Any,
) -> tuple[list[ChunkDocument], list[ChunkDocument]]:
    fields = ticket_data.get("fields", {}) or {}
    desc_text = clean_text(extract_description_text(fields.get("description")))
    comments = extract_substantive_comments(fields.get("comment") or {}, max_comments=3)

    if not desc_text and not comments:
        return [], []

    body_parts: list[tuple[str, str]] = []
    for paragraph in split_paragraphs(desc_text):
        body_parts.append(("description", paragraph))
    for comment in comments:
        for paragraph in split_paragraphs(comment):
            body_parts.append(("comment", paragraph))
    body_parts = body_parts[:_MAX_BODY_PARAGRAPHS]

    if not body_parts:
        return [], []

    overview = generate_body_overview(ticket_key, desc_text, comments, llm_client, cfg)
    title = str(fields.get("summary") or ticket_key)
    section_text = f"## Ticket Body: {title}\n\n{overview}" if overview else f"## Ticket Body: {title}"

    attachment_id = ""
    section_uid = ChunkDocument.make_uid(ticket_key, attachment_id, "section", "ticket-body")

    section_doc = ChunkDocument(
        chunk_uid=section_uid,
        ticket_id=ticket_key,
        level="section",
        parent_uid="",
        attachment_id=attachment_id,
        section_uid=section_uid,
        text=section_text,
        source="section",
        section_title=title,
        doc_role="ticket_body",
        weight_multiplier=0.9,
        extraction_confidence=1.0,
        word_count=len(section_text.split()),
    )

    leaf_docs: list[ChunkDocument] = []
    for idx, (source, text) in enumerate(body_parts):
        leaf_docs.append(
            ChunkDocument(
                chunk_uid=ChunkDocument.make_uid(ticket_key, attachment_id, source, f"body-{idx}"),
                ticket_id=ticket_key,
                level="chunk",
                parent_uid=section_uid,
                attachment_id=attachment_id,
                section_uid=section_uid,
                text=text,
                source=source,
                section_title=title,
                doc_role="description" if source == "description" else "comment",
                chunk_index=idx,
                weight_multiplier=0.9 if source == "description" else 0.6,
                extraction_confidence=1.0,
                word_count=len(text.split()),
            )
        )

    return [section_doc], leaf_docs


def split_paragraphs(text: str) -> list[str]:
    if not text:
        return []
    paragraphs = re.split(r"\n{2,}", text)
    return [paragraph.strip() for paragraph in paragraphs if len(paragraph.split()) >= _BODY_PARAGRAPH_MIN_WORDS]


def generate_body_overview(
    ticket_key: str,
    description: str,
    comments: list[str],
    llm_client: Optional[Any],
    cfg: Any,
) -> str:
    if llm_client is None or getattr(cfg, "skip_llm_summary", False):
        return ""
    if not description and not comments:
        return ""

    body = description
    if comments:
        body = body + "\n\n" + "\n\n".join(comments)
    body = body[:6000]

    prompt = (
        "Write a 2-3 sentence plain-language overview of the following Jira ticket "
        "body. Focus on what business problem is being described and what capability "
        "or change is being requested.\n\n"
        f"Ticket {ticket_key} body:\n{body}"
    )
    try:
        return clean_text(
            complete_text(
                prompt,
                llm_client,
                model=getattr(cfg, "llm_model", None),
                max_output_tokens=400,
                temperature=0.2,
            )
            or ""
        )
    except Exception as exc:
        logger.info("Body overview generation failed: %s", exc)
        return ""


def unique_attachments(attachments: list[Optional[dict]]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for att in attachments:
        if not att:
            continue
        att_id = attachment_key(att)
        if not att_id or att_id in seen:
            continue
        seen.add(att_id)
        out.append(att)
    return out


def attachment_key(att: dict) -> str:
    return str(att.get("id") or att.get("attachment_id") or att.get("filename") or "")


def ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def source_for_ext(ext: str) -> str:
    if ext in {"pptx", "ppt"}:
        return "pptx_slide"
    if ext == "pdf":
        return "pdf_page"
    return "docx_section"


def build_header_hierarchy(
    section_title: str,
    slide_num: Optional[int],
    page_num: Optional[int],
    page_range: Optional[list[int]],
    slide_range: Optional[list[int]],
) -> str:
    parts: list[str] = []
    if section_title:
        parts.append(section_title)
    locator = locator_label(
        slide_num=slide_num,
        page_num=page_num,
        page_range=page_range,
        slide_range=slide_range,
    )
    if locator:
        parts.append(locator)
    return " > ".join(parts)


def locator_label(
    *,
    slide_num: Optional[int] = None,
    page_num: Optional[int] = None,
    page_range: Optional[list[int]] = None,
    slide_range: Optional[list[int]] = None,
) -> str:
    if page_num is not None:
        return f"page:{page_num}"
    if slide_num is not None:
        return f"slide:{slide_num}"
    if page_range:
        return f"pages:{page_range[0]}-{page_range[-1]}"
    if slide_range:
        return f"slides:{slide_range[0]}-{slide_range[-1]}"
    return ""


__all__ = [
    "attachment_key",
    "build_from_attachments",
    "build_from_ticket_body",
    "extract_attachment_leaves",
    "ext_of",
    "prefetch_attachment_bytes",
    "unique_attachments",
]
