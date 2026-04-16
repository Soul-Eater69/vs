"""
Chunk ingestion pipeline → Azure AI Search Index C.

Flow:
  1. RESOLVE      — find the idea card (attachment or description link) or None
  2. EXTRACT      — slide/page/section chunks from the idea-card bytes
                    OR synthesize a ticket_body document from description + comments
  3. GROUP        — roll consecutive leaf chunks into logical sections (hierarchy parent)
  4. MATERIALIZE  — build ChunkDocuments with stable UIDs and ticket→attachment→section→chunk hierarchy
  5. LABEL        — attach VS labels from Jira issue links (denormalized onto every chunk)
  6. EMBED        — encode each chunk's text to a vector

Returns a HierarchicalTicketResult ready for bulk upsert via all_documents().
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from content.formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)
from content.schemas import (
    ChunkDocument,
    HierarchicalTicketResult,
)
from ingestion.idea_card import IdeaCard, resolve_idea_card
from processing.chunking import build_section_chunks

logger = logging.getLogger(__name__)

_BODY_PARAGRAPH_MIN_WORDS = 15
_MAX_BODY_PARAGRAPHS = 40


async def ingest_ticket_chunks(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> HierarchicalTicketResult:
    """Full chunk pipeline for a single ticket — fetches first."""
    cfg = _default_cfg(cfg)
    ticket_data = await jira_client.get_ticket_data(ticket_key, config=cfg)
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

    idea_card = await resolve_idea_card(ticket_data, jira_client, cfg)

    if idea_card.is_present:
        sections, leaves = _build_from_idea_card(
            ticket_key=ticket_key,
            idea_card=idea_card,
            cfg=cfg,
        )
    else:
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
# Case 1: idea card present → extract + section-group
# ---------------------------------------------------------------------------

def _build_from_idea_card(
    ticket_key: str,
    idea_card: IdeaCard,
    cfg: Any,
) -> tuple[list[ChunkDocument], list[ChunkDocument]]:
    leaf_dicts = _extract_leaves(idea_card, cfg)
    if not leaf_dicts:
        return [], []

    section_dicts = build_section_chunks(leaf_dicts) or []

    attachment_id = _attachment_id(idea_card)
    doc_role = "primary_idea_card" if idea_card.origin == "attachment" else "primary_fallback"

    section_docs: list[ChunkDocument] = []
    section_uid_by_start: dict[int, str] = {}

    for section in section_dicts:
        section_uid = ChunkDocument.make_uid(
            ticket_key, attachment_id, "section", section["chunk_id"],
        )
        section_docs.append(
            ChunkDocument(
                chunk_uid=section_uid,
                ticket_id=ticket_key,
                level="section",
                parent_uid="",
                attachment_id=attachment_id,
                section_uid=section_uid,
                text=section["text"],
                source="section",
                section_title=section.get("section_title", ""),
                doc_role=doc_role,
                slide_range=section.get("slide_range"),
                page_range=section.get("page_range"),
                weight_multiplier=float(section.get("weight_multiplier", 1.0)),
                extraction_confidence=float(section.get("extraction_confidence", 1.0)),
                word_count=int(section.get("word_count", 0)),
            )
        )
        start = (section.get("slide_range") or section.get("page_range") or [0])[0]
        section_uid_by_start[int(start or 0)] = section_uid

    leaf_docs: list[ChunkDocument] = []
    for idx, leaf in enumerate(leaf_dicts):
        if leaf.get("is_boilerplate"):
            continue
        position = leaf.get("slide_num") or leaf.get("page_num") or 0
        parent_uid = _find_parent_uid(section_uid_by_start, int(position))
        leaf_docs.append(
            ChunkDocument(
                chunk_uid=ChunkDocument.make_uid(
                    ticket_key, attachment_id, str(leaf.get("source", "chunk")), leaf["chunk_id"],
                ),
                ticket_id=ticket_key,
                level="chunk",
                parent_uid=parent_uid,
                attachment_id=attachment_id,
                section_uid=parent_uid,
                text=leaf.get("text", ""),
                source=leaf.get("source", "pptx_slide"),
                section_title=leaf.get("slide_title") or leaf.get("section_title", ""),
                doc_role=doc_role,
                chunk_index=idx,
                slide_num=leaf.get("slide_num"),
                page_num=leaf.get("page_num"),
                weight_multiplier=float(leaf.get("weight_multiplier", 1.0)),
                extraction_confidence=float(leaf.get("extraction_confidence", 1.0)),
                word_count=int(leaf.get("word_count", 0)),
            )
        )

    return section_docs, leaf_docs


def _extract_leaves(idea_card: IdeaCard, cfg: Any) -> list[dict]:
    ext = idea_card.ext.lower()
    file_bytes = idea_card.file_bytes or b""
    try:
        if ext in ("pptx", "ppt"):
            from processing.extraction.pptx import extract_pptx
            return extract_pptx(
                file_bytes,
                max_slides=int(getattr(cfg, "max_slides", 60) or 60),
            ).get("chunks", []) or []
        if ext == "pdf":
            from processing.extraction.pdf import extract_pdf
            return extract_pdf(file_bytes, ocr_enabled=False).get("chunks", []) or []
        if ext in ("docx", "doc"):
            from processing.extraction.docx import extract_docx
            return extract_docx(file_bytes).get("chunks", []) or []
    except Exception as exc:
        logger.warning("Idea-card extraction failed (%s): %s", idea_card.filename, exc)
    return []


def _find_parent_uid(section_uid_by_start: dict[int, str], position: int) -> str:
    if not section_uid_by_start:
        return ""
    applicable = [start for start in section_uid_by_start if start <= position]
    if not applicable:
        # Leaf precedes any section start — attach to the first section
        return section_uid_by_start[min(section_uid_by_start)]
    return section_uid_by_start[max(applicable)]


# ---------------------------------------------------------------------------
# Case 2: no idea card → build from description + comments
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

    body_parts: list[tuple[str, str]] = []  # (source, text)
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

    attachment_id = ""  # no attachment — ticket body has empty attachment_id
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
                chunk_uid=ChunkDocument.make_uid(
                    ticket_key, attachment_id, src, f"body-{idx}",
                ),
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
    """Short LLM overview of the ticket body when no idea card exists."""
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
        from clients.llm import complete_text
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


# ---------------------------------------------------------------------------
# Embedding (shared)
# ---------------------------------------------------------------------------

def _embed_chunks(docs: list[ChunkDocument], embedding_client: Any, cfg: Any) -> None:
    from clients.embedding import embed_batch

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

def _attachment_id(idea_card: IdeaCard) -> str:
    if idea_card.attachment:
        return str(
            idea_card.attachment.get("id")
            or idea_card.attachment.get("attachment_id")
            or idea_card.filename
        )
    return idea_card.filename or ""


def _default_cfg(cfg: Optional[Any]) -> Any:
    if cfg is not None:
        return cfg
    from pipelines.jira_batch.config import JiraIngestionConfig
    return JiraIngestionConfig()
