"""Pydantic models for idea-card metadata-only audit (v2)."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class IdeaCardPresence(str, Enum):
    confirmed = "confirmed"
    likely = "likely"
    possible = "possible"
    absent = "absent"
    inaccessible = "inaccessible"
    ambiguous = "ambiguous"


class LinkAccessStatus(str, Enum):
    accessible = "accessible"
    auth_required = "auth_required"
    forbidden = "forbidden"
    not_found = "not_found"
    timeout = "timeout"
    connection_error = "connection_error"
    unsupported_scheme = "unsupported_scheme"
    sharepoint_auth_required = "sharepoint_auth_required"
    skipped = "skipped"
    unknown_error = "unknown_error"


class LinkCandidate(BaseModel):
    id: str
    url: str
    url_host: str = ""
    url_path_hint: str = ""
    raw_context: str = ""
    mentioned_as_idea_card: bool = False
    access_status: LinkAccessStatus = LinkAccessStatus.skipped
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    final_url: Optional[str] = None
    not_accessible_reason: str = ""
    # Fuzzy assessment (set by fuzzy classifier based on metadata)
    is_useful_fuzzy: bool = False
    usefulness_reason_fuzzy: str = ""
    # LLM assessment (set independently by LLM classifier)
    is_useful_llm: Optional[bool] = None
    usefulness_reason_llm: Optional[str] = None


class AttachmentCandidate(BaseModel):
    id: str
    filename: str = ""
    extension: str = ""
    size: Optional[int] = None
    url: Optional[str] = None
    mentioned_as_idea_card: bool = False
    # Fuzzy scores from attachment router (metadata-only)
    fuzzy_score: float = 0.0
    fuzzy_role: str = ""
    fuzzy_reasons: list[str] = Field(default_factory=list)
    is_useful_fuzzy: bool = False
    usefulness_reason_fuzzy: str = ""
    # LLM assessment (set independently by LLM classifier)
    is_useful_llm: Optional[bool] = None
    usefulness_reason_llm: Optional[str] = None


class FuzzyDecision(BaseModel):
    presence: str
    confidence: float
    primary_source_type: Optional[str] = None
    primary_source_id: Optional[str] = None
    primary_source_name: Optional[str] = None
    reasons: list[str] = Field(default_factory=list)
    needs_llm_review: bool = False


class LlmDecision(BaseModel):
    presence: str
    confidence: float
    primary_source_type: Optional[str] = None
    primary_source_id: Optional[str] = None
    primary_source_name: Optional[str] = None
    useful_link_ids: list[str] = Field(default_factory=list)
    not_useful_link_ids: list[str] = Field(default_factory=list)
    needs_supporting_attachments: bool = False
    supporting_candidate_ids: list[str] = Field(default_factory=list)
    should_extract_description: bool = True
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)


class ComparisonResult(BaseModel):
    # exact_match | compatible_match | major_disagreement | llm_skipped
    presence_match_type: str
    primary_source_match: Optional[bool] = None
    # aligned | diverged | llm_skipped
    fuzzy_vs_llm: str
    is_major_disagreement: bool = False
    is_fuzzy_false_negative_proxy: bool = False  # fuzzy=absent, llm=present
    is_fuzzy_false_positive_proxy: bool = False  # fuzzy=present, llm=absent
    needs_manual_review: bool = False
    comparison_reason: str = ""


class FinalRecommendation(BaseModel):
    presence: str
    primary_source_type: Optional[str] = None
    primary_source_id: Optional[str] = None
    primary_source_name: Optional[str] = None
    extract_description: bool = False
    extract_primary_attachment: bool = False
    extract_supporting_attachments: bool = False
    manual_review: bool = False


class DescriptionSignals(BaseModel):
    has_idea_card_statement: bool = False
    statements: list[str] = Field(default_factory=list)
    description_excerpt: str = ""


class LinkSummary(BaseModel):
    total_links: int = 0
    useful_links: int = 0
    not_useful_links: int = 0
    accessible_links: int = 0
    inaccessible_links: int = 0


class AttachmentSummary(BaseModel):
    total_attachments: int = 0
    candidate_attachments: int = 0
    ppt_or_deck_like_count: int = 0


class TicketAuditRecord(BaseModel):
    ticket_id: str
    title: str = ""
    value_streams: list[str] = Field(default_factory=list)
    metadata_only: bool = True
    description_signals: DescriptionSignals = Field(default_factory=DescriptionSignals)
    link_summary: LinkSummary = Field(default_factory=LinkSummary)
    links: list[LinkCandidate] = Field(default_factory=list)
    attachment_summary: AttachmentSummary = Field(default_factory=AttachmentSummary)
    attachments: list[AttachmentCandidate] = Field(default_factory=list)
    fuzzy_decision: FuzzyDecision
    llm_decision: Optional[LlmDecision] = None
    llm_error: Optional[str] = None
    comparison: Optional[ComparisonResult] = None
    final_recommendation: FinalRecommendation
    errors: list[str] = Field(default_factory=list)


class AuditRunMetadata(BaseModel):
    source: str
    total_tickets: int
    probe_links: bool
    enable_llm: bool
    llm_every_ticket: bool
    metadata_only: bool
    download_attachments: bool
    created_at: str


class AuditSummary(BaseModel):
    total_tickets: int = 0
    ticket_presence_counts_final: dict = Field(default_factory=dict)
    ticket_presence_counts_fuzzy: dict = Field(default_factory=dict)
    ticket_presence_counts_llm: dict = Field(default_factory=dict)
    link_totals: dict = Field(default_factory=dict)
    link_access_counts: dict = Field(default_factory=dict)
    link_usefulness_reason_counts: dict = Field(default_factory=dict)
    attachment_extension_counts: dict = Field(default_factory=dict)
    comparison_counts: dict = Field(default_factory=dict)
    fuzzy_vs_llm_proxy_accuracy: dict = Field(default_factory=dict)


class AuditOutput(BaseModel):
    metadata: AuditRunMetadata
    summary: AuditSummary
    tickets: dict[str, TicketAuditRecord] = Field(default_factory=dict)
