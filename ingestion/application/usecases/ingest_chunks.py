"""
Chunk ingestion pipeline → Azure AI Search Index C.

Flow:
  1. ROUTE        — score viable attachments and keep the best chunk candidates
  2. EXTRACT      — slide/page/section chunks from all retained attachment bytes
                    OR synthesize a ticket_body document from description + comments
  3. GROUP        — roll consecutive leaf chunks into logical sections (hierarchy parent)
  4. MATERIALIZE  — build ChunkDocuments with stable UIDs and hierarchy
  5. LABEL        — attach VS labels from Jira issue links (denormalized onto every chunk)
  6. EMBED        — encode each chunk's text to a vector
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..content.formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)
from ..processing.attachment_routing import get_routing_candidates, route_attachments
from ..processing.chunking import build_section_chunks

from ...adapters.jira.fetch_compat import get_ticket_data_compat
from ...adapters.embeddings import embed_batch
from ...domain.tickets.documents import ChunkDocument, HierarchicalTicketResult

logger = logging.getLogger(__name__)

_BODY_PARAGRAPH_MIN_WORDS = 15
_MAX_BODY_PARAGRAPHS = 40
_PREFETCHABLE_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}


async def ingest_ticket_chunks(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> HierarchicalTicketResult:
    """Full chunk pipeline for a single ticket — fetches first."""
    cfg = _default_cfg(cfg)
    ticket_data = await get_ticket_data_compat(
        jira_client,
        ticket_key,
        config=cfg,
        llm_client=llm_client,
    )
    return await ingest_ticket_chunks_payload(
        ticket_data=ticket_data,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
    )


async def ingest_ticket_chunks_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> HierarchicalTicketResult:
    cfg = _default_cfg(cfg)
    ticket_key = str(ticket_data.get("key", ""))

    vs_ids = list(ticket_data.get("value_stream_ids") or [])
    vs_names = list(ticket_data.get("value_stream_names") or [])
    label_source = str(
        ticket_data.get("value_stream_label_source")
        or ticket_data.get("label_source")
        or "jira_issuelinks"
    )

    sections, leaves = await _build_from_attachments(
        ticket_key=ticket_key,
        ticket_data=ticket_data,
        jira_client=jira_client,
        cfg=cfg,
    )

    if not leaves:
        sections, leaves = _build_from_ticket_body(
            ticket_key=ticket_key,
            ticket_data=ticket_data,
            llm_client=llm_client,
            cfg=cfg,
        )

    for doc in sections + leaves:
        doc.value_stream_ids = list(vs_ids)
        doc.value_stream_names = list(vs_names)

    if embedding_client is not None and (sections or leaves):
        _embed_chunks(sections + leaves, embedding_client, cfg)

    return HierarchicalTicketResult(
        ticket_id=ticket_key,
        value_stream_ids=list(vs_ids),
        value_stream_names=list(vs_names),
        label_source=label_source,
        sections=sections,
        chunks=leaves,
    )


# ---------------------------------------------------------------------------
# Case 1: attachment-driven chunking
# ---------------------------------------------------------------------------

async def _build_from_attachments(
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
    prefetch_targets = _unique_attachments(
        ([seed_primary] if seed_primary else [])
        + get_routing_candidates(seed_artifact)
        + attachments
    )
    prefetched = await _prefetch_attachment_bytes(jira_client, prefetch_targets, cfg)
    if not prefetched:
        return [], []

    def cached_download(att: dict) -> bytes:
        att_id = _attachment_key(att)
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
    ordered_candidates = _unique_attachments(
        ([primary] if primary else []) + chunk_candidates
    )

    processed_docs: list[dict[str, Any]] = []
    max_docs = int(getattr(cfg, "max_chunk_attachments", 6) or 6)
    for att in ordered_candidates[:max_docs]:
        att_id = _attachment_key(att)
        file_bytes = prefetched.get(att_id)
        if file_bytes is None:
            try:
                file_bytes = await jira_client.download_attachment(att)
            except Exception as exc:
                logger.info("Attachment download skipped (%s): %s", att.get("filename"), exc)
                continue

        leaves = _extract_attachment_leaves(file_bytes or b"", att, cfg)
        leaves = [leaf for leaf in leaves if leaf.get("text") and not leaf.get("is_boilerplate")]
        if not leaves:
            continue
        processed_docs.append(
            {
                "attachment_id": att_id,
                "attachment_name": str(att.get("filename") or att_id),
                "attachment_type": str(att.get("ext") or _ext_of(att.get("filename", ""))),
                "source_url": str(att.get("content") or att.get("url") or ticket_key),
                "doc_role": _normalized_doc_role(att),
                "leaves": leaves,
            }
        )

    if not processed_docs:
        return [], []

    return _materialize_attachment_documents(ticket_key, processed_docs)


async def _prefetch_attachment_bytes(
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
        ext = _ext_of(att.get("filename", ""))
        size = int(att.get("size", 0) or 0)
        if ext not in _PREFETCHABLE_EXTENSIONS:
            continue
        if size and size > max_size:
            continue

        att_id = _attachment_key(att)
        if att_id in prefetched:
            continue
        try:
            prefetched[att_id] = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment prefetch skipped (%s): %s", att.get("filename"), exc)

    return prefetched


def _extract_attachment_leaves(file_bytes: bytes, attachment: dict, cfg: Any) -> list[dict]:
    ext = str(attachment.get("ext") or _ext_of(attachment.get("filename", ""))).lower()
    doc_role = _normalized_doc_role(attachment)
    weight = _doc_role_weight(doc_role)

    extracted = attachment.get("extracted") or {}
    try:
        if not extracted.get("chunks"):
            if ext in ("pptx", "ppt"):
                from ..processing.extraction.pptx import extract_pptx
                extracted = extract_pptx(
                    file_bytes,
                    max_slides=int(getattr(cfg, "max_slides", 60) or 60),
                )
            elif ext == "pdf":
                from ..processing.extraction.pdf import extract_pdf
                extracted = extract_pdf(
                    file_bytes,
                    ocr_enabled=bool(getattr(cfg, "ocr_enabled", False)),
                    max_pages=int(getattr(cfg, "max_pages", 60) or 60),
                )
            elif ext in ("docx", "doc"):
                from ..processing.extraction.docx import extract_docx
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
        source = str(raw.get("source") or _source_for_ext(ext))
        header_hierarchy = _build_header_hierarchy(
            section_title, slide_num, page_num, page_range, slide_range
        )

        leaves.append(
            {
                "chunk_id": str(raw.get("chunk_id") or f"{_attachment_key(attachment)}-{idx}"),
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
                "attachment_id": _attachment_key(attachment),
                "attachment_name": str(attachment.get("filename") or ""),
                "attachment_type": ext,
                "header_hierarchy": header_hierarchy,
            }
        )

    return leaves


def _materialize_attachment_documents(
    ticket_key: str,
    processed_docs: list[dict[str, Any]],
) -> tuple[list[ChunkDocument], list[ChunkDocument]]:
    section_docs: list[ChunkDocument] = []
    leaf_docs: list[ChunkDocument] = []

    for doc in processed_docs:
        leaves = list(doc.get("leaves") or [])
        if not leaves:
            continue

        if str(doc.get("attachment_type") or "").lower() in {"docx", "doc"}:
            section_dicts = _fallback_section_chunks(leaves, doc)
        else:
            section_dicts = build_section_chunks(leaves) or _fallback_section_chunks(leaves, doc)
        section_uid_by_start: dict[int, str] = {}

        for section_idx, section in enumerate(section_dicts):
            raw_chunk_id = str(section.get("chunk_id") or f"{doc['attachment_id']}-section-{section_idx}")
            section_uid = ChunkDocument.make_uid(
                ticket_key, doc["attachment_id"], "section", raw_chunk_id,
            )
            section_docs.append(
                ChunkDocument(
                    chunk_uid=section_uid,
                    ticket_id=ticket_key,
                    level="section",
                    parent_uid="",
                    attachment_id=doc["attachment_id"],
                    section_uid=section_uid,
                    text=str(section.get("text") or ""),
                    source="section",
                    section_title=str(section.get("section_title") or doc["attachment_name"]),
                    doc_role=str(doc.get("doc_role") or "supporting_doc"),
                    attachment_name=str(doc.get("attachment_name") or ""),
                    attachment_type=str(doc.get("attachment_type") or ""),
                    header_hierarchy=str(section.get("section_title") or doc["attachment_name"]),
                    source_url=str(doc.get("source_url") or ticket_key),
                    slide_range=section.get("slide_range"),
                    page_range=section.get("page_range"),
                    weight_multiplier=float(section.get("weight_multiplier", 1.0)),
                    extraction_confidence=float(section.get("extraction_confidence", 1.0)),
                    word_count=int(section.get("word_count", 0)),
                    chunk_provenance=_build_chunk_provenance(
                        source_type="section",
                        attachment_id=doc["attachment_id"],
                        attachment_name=str(doc.get("attachment_name") or ""),
                        attachment_type=str(doc.get("attachment_type") or ""),
                        chunk_id=raw_chunk_id,
                        chunk_index=section_idx,
                        page_range=section.get("page_range"),
                        slide_range=section.get("slide_range"),
                    ),
                )
            )
            start = _section_start(section, fallback=section_idx + 1)
            section_uid_by_start[int(start or 0)] = section_uid

        for leaf_idx, leaf in enumerate(leaves):
            position = int(leaf.get("slide_num") or leaf.get("page_num") or leaf_idx + 1)
            parent_uid = _find_parent_uid(section_uid_by_start, position) if section_uid_by_start else ""
            raw_chunk_id = str(leaf.get("chunk_id") or f"{doc['attachment_id']}-{leaf_idx}")
            leaf_docs.append(
                ChunkDocument(
                    chunk_uid=ChunkDocument.make_uid(
                        ticket_key,
                        doc["attachment_id"],
                        str(leaf.get("source", "chunk")),
                        raw_chunk_id,
                    ),
                    ticket_id=ticket_key,
                    level="chunk",
                    parent_uid=parent_uid,
                    attachment_id=doc["attachment_id"],
                    section_uid=parent_uid or "",
                    text=str(leaf.get("text") or ""),
                    source=str(leaf.get("source", "pptx_slide")),
                    section_title=str(leaf.get("section_title") or doc["attachment_name"]),
                    doc_role=str(doc.get("doc_role") or "supporting_doc"),
                    attachment_name=str(doc.get("attachment_name") or ""),
                    attachment_type=str(doc.get("attachment_type") or ""),
                    header_hierarchy=str(leaf.get("header_hierarchy") or ""),
                    source_url=str(doc.get("source_url") or ticket_key),
                    chunk_index=leaf_idx,
                    slide_num=leaf.get("slide_num"),
                    page_num=leaf.get("page_num"),
                    slide_range=leaf.get("slide_range"),
                    page_range=leaf.get("page_range"),
                    weight_multiplier=float(leaf.get("weight_multiplier", 1.0)),
                    extraction_confidence=float(leaf.get("extraction_confidence", 1.0)),
                    word_count=int(leaf.get("word_count", 0)),
                    chunk_provenance=_build_chunk_provenance(
                        source_type=str(leaf.get("source", "chunk")),
                        attachment_id=doc["attachment_id"],
                        attachment_name=str(doc.get("attachment_name") or ""),
                        attachment_type=str(doc.get("attachment_type") or ""),
                        chunk_id=raw_chunk_id,
                        chunk_index=leaf_idx,
                        page_num=leaf.get("page_num"),
                        slide_num=leaf.get("slide_num"),
                        page_range=leaf.get("page_range"),
                        slide_range=leaf.get("slide_range"),
                    ),
                )
            )

    return section_docs, leaf_docs


# ---------------------------------------------------------------------------
# Case 2: no extractable attachment → build from description + comments
# ---------------------------------------------------------------------------

def _build_from_ticket_body(
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
    for p in _split_paragraphs(desc_text):
        body_parts.append(("description", p))
    for c in comments:
        for p in _split_paragraphs(c):
            body_parts.append(("comment", p))
    body_parts = body_parts[:_MAX_BODY_PARAGRAPHS]

    if not body_parts:
        return [], []

    overview = _generate_body_overview(ticket_key, desc_text, comments, llm_client, cfg)
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
    for idx, (src, text) in enumerate(body_parts):
        leaf_docs.append(
            ChunkDocument(
                chunk_uid=ChunkDocument.make_uid(ticket_key, attachment_id, src, f"body-{idx}"),
                ticket_id=ticket_key,
                level="chunk",
                parent_uid=section_uid,
                attachment_id=attachment_id,
                section_uid=section_uid,
                text=text,
                source=src,
                section_title=title,
                doc_role="description" if src == "description" else "comment",
                chunk_index=idx,
                weight_multiplier=0.9 if src == "description" else 0.6,
                extraction_confidence=1.0,
                word_count=len(text.split()),
            )
        )

    return [section_doc], leaf_docs


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_chunks(docs: list[ChunkDocument], embedding_client: Any, cfg: Any) -> None:
    texts = [d.text for d in docs]
    try:
        vectors = embed_batch(
            texts,
            embedding_client,
            model=getattr(cfg, "embedding_model", None),
        )
    except Exception as exc:
        logger.warning("Embedding failed for %d chunks: %s", len(texts), exc)
        return

    for doc, vec in zip(docs, vectors or []):
        doc.embedding = list(vec or [])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_cfg(cfg: Optional[Any]) -> Any:
    if cfg is not None:
        return cfg
    from pipelines.jira_batch.config import JiraIngestionConfig
    return JiraIngestionConfig()


def _split_paragraphs(text: str) -> list[str]:
    if not text:
        return []
    paragraphs = re.split(r"\n{2,}", text)
    return [p.strip() for p in paragraphs if len(p.split()) >= _BODY_PARAGRAPH_MIN_WORDS]


def _generate_body_overview(
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
        from ...adapters.llm import complete_text
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


def _find_parent_uid(section_uid_by_start: dict[int, str], position: int) -> str:
    if not section_uid_by_start:
        return ""
    applicable = [start for start in section_uid_by_start if start <= position]
    if not applicable:
        return section_uid_by_start[min(section_uid_by_start)]
    return section_uid_by_start[max(applicable)]


def _fallback_section_chunks(leaves: list[dict], doc: dict[str, Any]) -> list[dict]:
    if not leaves:
        return []
    merged_text = "\n\n".join(str(leaf.get("text") or "") for leaf in leaves if leaf.get("text")).strip()
    if not merged_text:
        return []
    page_nums = [int(v) for v in (leaf.get("page_num") for leaf in leaves) if isinstance(v, int)]
    slide_nums = [int(v) for v in (leaf.get("slide_num") for leaf in leaves) if isinstance(v, int)]
    return [
        {
            "chunk_id": f"{doc['attachment_id']}-section-1",
            "text": f"## Section: {doc['attachment_name']}\n\n{merged_text}"[:8000],
            "section_title": str(doc.get("attachment_name") or "Attachment"),
            "page_range": [min(page_nums), max(page_nums)] if page_nums else None,
            "slide_range": [min(slide_nums), max(slide_nums)] if slide_nums else None,
            "weight_multiplier": _doc_role_weight(str(doc.get("doc_role") or "supporting_doc")),
            "extraction_confidence": min(
                [float(leaf.get("extraction_confidence", 1.0) or 1.0) for leaf in leaves]
            ),
            "word_count": len(merged_text.split()),
        }
    ]


def _unique_attachments(attachments: list[Optional[dict]]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for att in attachments:
        if not att:
            continue
        att_id = _attachment_key(att)
        if not att_id or att_id in seen:
            continue
        seen.add(att_id)
        out.append(att)
    return out


def _attachment_key(att: dict) -> str:
    return str(att.get("id") or att.get("attachment_id") or att.get("filename") or "")


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _normalized_doc_role(att: dict) -> str:
    role = str(att.get("doc_role") or "").strip()
    if role == "supporting":
        return "supporting_doc"
    if role in {"primary_idea_card", "primary_fallback"}:
        return role
    return "supporting_doc"


def _doc_role_weight(doc_role: str) -> float:
    if doc_role == "primary_idea_card":
        return 1.0
    if doc_role == "primary_fallback":
        return 0.9
    if doc_role == "supporting_doc":
        return 0.72
    return 0.5


def _source_for_ext(ext: str) -> str:
    if ext in {"pptx", "ppt"}:
        return "pptx_slide"
    if ext == "pdf":
        return "pdf_page"
    return "docx_section"


def _build_header_hierarchy(
    section_title: str,
    slide_num: Optional[int],
    page_num: Optional[int],
    page_range: Optional[list[int]],
    slide_range: Optional[list[int]],
) -> str:
    parts: list[str] = []
    if section_title:
        parts.append(section_title)
    locator = _locator_label(
        slide_num=slide_num, page_num=page_num,
        page_range=page_range, slide_range=slide_range,
    )
    if locator:
        parts.append(locator)
    return " > ".join(parts)


def _locator_label(
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


def _section_start(section: dict, fallback: int) -> int:
    page_range = section.get("page_range") or []
    slide_range = section.get("slide_range") or []
    if page_range:
        return int(page_range[0] or fallback)
    if slide_range:
        return int(slide_range[0] or fallback)
    return fallback


def _build_chunk_provenance(
    *,
    source_type: str,
    attachment_id: str,
    attachment_name: str,
    attachment_type: str,
    chunk_id: str,
    chunk_index: int,
    page_num: Optional[int] = None,
    slide_num: Optional[int] = None,
    page_range: Optional[list[int]] = None,
    slide_range: Optional[list[int]] = None,
) -> dict[str, Any]:
    return {
        "chunkId": chunk_id,
        "chunkIndex": chunk_index,
        "sourceType": source_type,
        "attachmentId": attachment_id,
        "attachmentName": attachment_name,
        "attachmentType": attachment_type,
        "pageRange": page_range or ([page_num] if page_num is not None else []),
        "slideRange": slide_range or ([slide_num] if slide_num is not None else []),
    }
