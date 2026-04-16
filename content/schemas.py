"""
Document schemas for the two ingestion modes:

  TicketSummaryDocument  — one LLM-structured summary per ticket (Index B)
  ChunkDocument          — one retrievable chunk per slide/page/paragraph (Index C)
  HierarchicalTicketResult — output of the chunk pipeline (sections + leaf chunks)

Both are indexed into Azure AI Search. The summary index is used for VS
prediction. The chunk index is used for RAG / evidence retrieval.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ChunkLevel = Literal["section", "chunk"]
ChunkSource = Literal[
    "pptx_slide", "pdf_page", "docx_section",
    "section",          # rolled-up section (parent of slides)
    "description",
    "comment",
]


@dataclass
class TicketSummaryDocument:
    """
    Structured summary of one Jira idea-card ticket.

    Produced by ingestion, upserted to Azure AI Search Index B.
    Value stream labels come from Jira issue links resolved at ingest time.
    """

    ticket_id: str

    # Core LLM-extracted fields (searchable)
    summary_text: str               # Dense plain-language summary
    business_problem: str           # What problem is being solved
    business_capability: str        # What capability/process is being changed
    key_terms: list[str]            # Domain vocabulary extracted from the card

    # Ground-truth VS labels (supervision)
    value_stream_ids: list[str] = field(default_factory=list)
    value_stream_names: list[str] = field(default_factory=list)
    label_source: str = "jira_issuelinks"
    direct_vs_names: list[str] = field(default_factory=list)
    implied_vs_names: list[str] = field(default_factory=list)
    value_streams: list[dict[str, str]] = field(default_factory=list)

    # Vector (populated after embedding)
    summary_embedding: list[float] = field(default_factory=list)

    def to_index_doc(self) -> dict:
        """Serialize to Azure AI Search document format."""
        return {
            "ticket_id": self.ticket_id,
            "summary_text": self.summary_text,
            "business_problem": self.business_problem,
            "business_capability": self.business_capability,
            "key_terms": self.key_terms,
            "value_stream_ids": self.value_stream_ids,
            "value_stream_names": self.value_stream_names,
            "label_source": self.label_source,
            "direct_vs_names": self.direct_vs_names,
            "implied_vs_names": self.implied_vs_names,
            "value_streams": self.value_streams,
            "summary_embedding": self.summary_embedding,
        }

    @classmethod
    def from_index_doc(cls, doc: dict) -> "TicketSummaryDocument":
        return cls(
            ticket_id=doc["ticket_id"],
            summary_text=doc.get("summary_text", ""),
            business_problem=doc.get("business_problem", ""),
            business_capability=doc.get("business_capability", ""),
            key_terms=doc.get("key_terms", []),
            value_stream_ids=doc.get("value_stream_ids", []),
            value_stream_names=doc.get("value_stream_names", []),
            label_source=doc.get("label_source", "jira_issuelinks"),
            direct_vs_names=doc.get("direct_vs_names", []),
            implied_vs_names=doc.get("implied_vs_names", []),
            value_streams=doc.get("value_streams", []),
            summary_embedding=doc.get("summary_embedding", []),
        )


# ---------------------------------------------------------------------------
# Chunk schema (hierarchical chunk ingestion)
# ---------------------------------------------------------------------------

@dataclass
class ChunkDocument:
    """
    One retrievable unit in the hierarchical chunk index (Index C).

    Hierarchy encoded as fields — enables filtering by level and parent traversal:

      ticket_id → attachment_id → section_uid → chunk_uid

    level="section"  : rolled-up section (parent node, no parent_uid)
    level="chunk"    : leaf slide/page/paragraph (parent_uid = section_uid)
    """

    chunk_uid: str          # stable sha256 of (ticket_id, attachment_id, source, chunk_id)
    ticket_id: str

    # Hierarchy
    level: ChunkLevel
    parent_uid: str         # section_uid for leaf chunks; empty for sections
    attachment_id: str      # empty for description / comment chunks
    section_uid: str        # which section group this belongs to

    # Content
    text: str
    source: ChunkSource
    section_title: str
    doc_role: str           # primary_idea_card | supporting_doc | description | comment

    # Position within attachment
    attachment_name: str = ""
    attachment_type: str = ""
    header_hierarchy: str = ""
    source_url: str = ""
    chunk_index: int = 0
    slide_num: Optional[int] = None
    page_num: Optional[int] = None
    slide_range: Optional[list[int]] = None
    page_range: Optional[list[int]] = None
    # Quality
    weight_multiplier: float = 1.0
    extraction_confidence: float = 1.0
    word_count: int = 0

    # VS labels (denormalized from ticket for filtered search)
    value_stream_ids: list[str] = field(default_factory=list)
    value_stream_names: list[str] = field(default_factory=list)

    # Vector
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
    """
    Output of the chunk ingestion pipeline.

    Contains two flat lists ready to upsert to Azure AI Search:
      sections  — section-level ChunkDocuments (parent nodes)
      chunks    — leaf ChunkDocuments (slide / page / paragraph)

    Call all_documents() to get everything merged for a single bulk upsert.
    """

    ticket_id: str
    value_stream_ids: list[str]
    value_stream_names: list[str]
    label_source: str

    sections: list[ChunkDocument] = field(default_factory=list)
    chunks: list[ChunkDocument] = field(default_factory=list)

    def all_documents(self) -> list[dict]:
        """Flat list of index docs (sections first, then leaf chunks)."""
        return [d.to_index_doc() for d in self.sections + self.chunks]
