"""Data models for the historical enrichment pipeline."""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class InferenceType(str, Enum):
    DIRECT = "direct"
    IMPLIED = "implied"


class RawTicket(BaseModel):
    ticket_id: str
    title: str = ""
    raw_text: str = ""
    description: str = ""
    value_stream_labels: List[str] = Field(default_factory=list)
    extraction_source: str = ""
    char_count: int = 0


class VSClassification(BaseModel):
    vs_name: str = Field(description="Value stream name as labeled on the ticket")
    inference_type: InferenceType = Field(
        description="'direct' if ticket explicitly addresses this VS domain, "
        "'implied' if upstream/downstream/adjacent"
    )
    reason: str = Field(description="One sentence: why, and what makes it direct vs implied")


class TicketEnrichmentResult(BaseModel):
    summary: str = Field(
        description="2-4 sentence business summary. Capture: problem, goal, "
        "stakeholders, domains, outcomes. Preserve specific terminology."
    )
    domain_tags: List[str] = Field(description="5-10 healthcare domain/function tags")
    vs_classifications: List[VSClassification] = Field(
        description="Classification of each attached value stream"
    )


class VSAttachment(BaseModel):
    vs_name: str
    vs_id: str = ""
    inference_type: InferenceType
    reason: str


class EnrichedTicket(BaseModel):
    ticket_id: str
    title: str = ""
    summary: str = ""
    domain_tags: List[str] = Field(default_factory=list)
    value_streams: List[VSAttachment] = Field(default_factory=list)
    raw_text_preview: str = ""
    value_stream_labels: List[str] = Field(default_factory=list)
    extraction_source: str = ""
    enrichment_model: str = ""
    char_count: int = 0
    enrichment_status: str = "pending"
    summary_embedding: Optional[List[float]] = None
