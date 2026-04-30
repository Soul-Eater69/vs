"""Deprecated chunk ingestion document schemas.

Moved out of the active package when ingestion was reduced to summary-only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ChunkLevel = Literal["section", "chunk"]
ChunkSource = Literal[
    "pptx_slide",
    "pdf_page",
    "docx_section",
    "section",
    "description",
    "comment",
]


@dataclass
class ChunkDocument:
    chunk_uid: str
    ticket_id: str
    level: ChunkLevel
    parent_uid: str
    attachment_id: str
    section_uid: str
    text: str
    source: ChunkSource
    section_title: str
    doc_role: str
    attachment_name: str = ""
    attachment_type: str = ""
    header_hierarchy: str = ""
    source_url: str = ""
    chunk_index: int = 0
    slide_num: Optional[int] = None
    page_num: Optional[int] = None
    slide_range: Optional[list[int]] = None
    page_range: Optional[list[int]] = None
    weight_multiplier: float = 1.0
    extraction_confidence: float = 1.0
    word_count: int = 0
    value_stream_ids: list[str] = field(default_factory=list)
    value_stream_names: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    chunk_provenance: dict[str, Any] = field(default_factory=dict)

    def to_index_doc(self) -> dict:
        chunk_provenance = self.chunk_provenance or {
            "chunkId": self.chunk_uid,
            "chunkIndex": self.chunk_index,
            "sourceType": self.source,
            "attachmentId": self.attachment_id,
            "attachmentName": self.attachment_name,
            "attachmentType": self.attachment_type,
            "pageRange": self.page_range or ([self.page_num] if self.page_num is not None else []),
            "slideRange": self.slide_range or ([self.slide_num] if self.slide_num is not None else []),
        }
        return {
            "chunk_uid": self.chunk_uid,
            "ticket_id": self.ticket_id,
            "level": self.level,
            "parent_uid": self.parent_uid,
            "attachment_id": self.attachment_id,
            "attachment_name": self.attachment_name,
            "attachment_type": self.attachment_type,
            "section_uid": self.section_uid,
            "text": self.text,
            "source": self.source,
            "section_title": self.section_title,
            "header_hierarchy": self.header_hierarchy,
            "source_url": self.source_url,
            "doc_role": self.doc_role,
            "chunk_index": self.chunk_index,
            "slide_num": self.slide_num,
            "page_num": self.page_num,
            "slide_range": self.slide_range,
            "page_range": self.page_range,
            "weight_multiplier": self.weight_multiplier,
            "extraction_confidence": self.extraction_confidence,
            "word_count": self.word_count,
            "value_stream_ids": self.value_stream_ids,
            "value_stream_names": self.value_stream_names,
            "embedding": self.embedding,
            "chunkProvenance": chunk_provenance,
        }

    @staticmethod
    def make_uid(ticket_id: str, attachment_id: str, source: str, chunk_id: str) -> str:
        raw = f"{ticket_id}::{attachment_id}::{source}::{chunk_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass
class HierarchicalTicketResult:
    ticket_id: str
    value_stream_ids: list[str]
    value_stream_names: list[str]
    label_source: str
    sections: list[ChunkDocument] = field(default_factory=list)
    chunks: list[ChunkDocument] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def all_documents(self) -> list[dict]:
        return [doc.to_index_doc() for doc in self.sections + self.chunks]
