"""Attachment leaf/section materialization for chunk ingestion."""

from __future__ import annotations

from typing import Any

from vs_app.modules.tickets.documents import ChunkDocument

from ..chunks.hierarchy import find_parent_uid, section_start
from ..chunks.mapper import build_chunk_provenance
from ..chunks.section_builder import build_section_chunks
from .roles import doc_role_weight


def materialize_attachment_documents(
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
            section_dicts = fallback_section_chunks(leaves, doc)
        else:
            section_dicts = build_section_chunks(leaves) or fallback_section_chunks(leaves, doc)
        section_uid_by_start: dict[int, str] = {}

        for section_idx, section in enumerate(section_dicts):
            raw_chunk_id = str(section.get("chunk_id") or f"{doc['attachment_id']}-section-{section_idx}")
            section_uid = ChunkDocument.make_uid(
                ticket_key,
                doc["attachment_id"],
                "section",
                raw_chunk_id,
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
                    chunk_provenance=build_chunk_provenance(
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
            start = section_start(section, fallback=section_idx + 1)
            section_uid_by_start[int(start or 0)] = section_uid

        for leaf_idx, leaf in enumerate(leaves):
            position = int(leaf.get("slide_num") or leaf.get("page_num") or leaf_idx + 1)
            parent_uid = (
                find_parent_uid(section_uid_by_start, position) if section_uid_by_start else ""
            )
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
                    chunk_provenance=build_chunk_provenance(
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


def fallback_section_chunks(leaves: list[dict], doc: dict[str, Any]) -> list[dict]:
    if not leaves:
        return []
    merged_text = "\n\n".join(str(leaf.get("text") or "") for leaf in leaves if leaf.get("text")).strip()
    if not merged_text:
        return []
    page_nums = [int(value) for value in (leaf.get("page_num") for leaf in leaves) if isinstance(value, int)]
    slide_nums = [int(value) for value in (leaf.get("slide_num") for leaf in leaves) if isinstance(value, int)]
    return [
        {
            "chunk_id": f"{doc['attachment_id']}-section-1",
            "text": f"## Section: {doc['attachment_name']}\n\n{merged_text}"[:8000],
            "section_title": str(doc.get("attachment_name") or "Attachment"),
            "page_range": [min(page_nums), max(page_nums)] if page_nums else None,
            "slide_range": [min(slide_nums), max(slide_nums)] if slide_nums else None,
            "weight_multiplier": doc_role_weight(str(doc.get("doc_role") or "supporting_doc")),
            "extraction_confidence": min(
                [float(leaf.get("extraction_confidence", 1.0) or 1.0) for leaf in leaves]
            ),
            "word_count": len(merged_text.split()),
        }
    ]


__all__ = ["fallback_section_chunks", "materialize_attachment_documents"]
