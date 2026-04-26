"""Pydantic schemas for LLM-generated structured outputs.

One module per output domain; import from this package directly.
"""

from .idea_card import (
    AttachmentAssessment,
    CandidateKind,
    IdeaCardAuditDecision,
    LinkAssessment,
    Presence,
)
from .summary import SummaryOutput
from .value_stream import (
    InferenceType,
    SelectedValueStream,
    SelectionResult,
    VsClassificationItem,
    VsClassificationResult,
    VsVerifierMapping,
    VsVerifierResult,
)

__all__ = [
    # idea_card
    "AttachmentAssessment",
    "CandidateKind",
    "IdeaCardAuditDecision",
    "LinkAssessment",
    "Presence",
    # summary
    "SummaryOutput",
    # value_stream
    "InferenceType",
    "SelectedValueStream",
    "SelectionResult",
    "VsClassificationItem",
    "VsClassificationResult",
    "VsVerifierMapping",
    "VsVerifierResult",
]
