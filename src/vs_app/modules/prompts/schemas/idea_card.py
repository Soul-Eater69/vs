"""Pydantic models for the idea_card_audit LLM output.

Mirrors the schema declared in `prompt_yaml/idea_card_audit.yaml`.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Presence = Literal["confirmed", "likely", "possible", "absent", "inaccessible", "ambiguous"]
CandidateKind = Literal["attachment", "link"]


class LinkAssessment(BaseModel):
    id: str
    is_useful: bool = False
    reason: str = ""


class AttachmentAssessment(BaseModel):
    id: str
    is_useful: bool = False
    reason: str = ""


class IdeaCardAuditDecision(BaseModel):
    """Output of the idea_card_audit prompt."""

    presence: Presence = "absent"
    confidence: float = 0.0
    primary_candidate_kind: Optional[CandidateKind] = None
    primary_candidate_id: Optional[str] = None
    primary_candidate_name: Optional[str] = None
    needs_supporting_attachments: bool = False
    supporting_candidate_ids: list[str] = Field(default_factory=list)
    link_assessments: list[LinkAssessment] = Field(default_factory=list)
    attachment_assessments: list[AttachmentAssessment] = Field(default_factory=list)
    inaccessible_but_relevant_link_ids: list[str] = Field(default_factory=list)
    should_extract_description: bool = True
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "AttachmentAssessment",
    "CandidateKind",
    "IdeaCardAuditDecision",
    "LinkAssessment",
    "Presence",
]
