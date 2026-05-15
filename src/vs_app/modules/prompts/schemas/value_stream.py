"""Pydantic models for value-stream LLM outputs.

Covers three prompts:
  - value_stream_classification.yaml → VsClassificationResult
  - jira_value_stream_verifier.yaml  → VsVerifierResult
  - review_pool_selection.yaml       → ReviewPoolPickResult
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

InferenceType = Literal["direct", "implied"]


# ---------------------------------------------------------------------------
# value_stream_classification.yaml
# ---------------------------------------------------------------------------

class VsClassificationItem(BaseModel):
    vs_name: str
    inference_type: InferenceType = "direct"
    reason: str = ""


class VsClassificationResult(BaseModel):
    """Output of the value_stream_classification prompt."""

    value_streams: list[VsClassificationItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# jira_value_stream_verifier.yaml
# ---------------------------------------------------------------------------

class VsVerifierMapping(BaseModel):
    raw_name: str
    approved_value_stream: Optional[str] = None


class VsVerifierResult(BaseModel):
    """Output of the jira_value_stream_verifier prompt."""

    mappings: list[VsVerifierMapping] = Field(default_factory=list)


# Slim schema for the review-pool pass: the model picks IDs + confidence + a short
# rationale. entity_name is filled in deterministically post-call from the candidate
# dict so the LLM can't drop rows via name paraphrasing.
# Fields are optional with defaults so a partial/empty tool call from the gateway
# doesn't raise; downstream code skips picks with empty entity_id and falls back to
# a templated reason if the LLM omits one.
class ReviewPoolPick(BaseModel):
    entity_id: str = Field(default="", description="The entity ID of the picked value stream")
    confidence: float = Field(
        default=0.5,
        description="0.8-1.0 strong, 0.5-0.7 partial, 0.3-0.4 weak but plausible",
    )
    reason: str = Field(
        default="",
        description=(
            "One short sentence (max ~140 characters) of business rationale: why this "
            "value stream maps to the idea card. Do not mention scores, ranks, or lanes."
        ),
    )
    selection_type: str = Field(
        default="",
        description="Use 'direct' for clear idea-card matches or 'implied' for operational impacts.",
    )


class ReviewPoolPickResult(BaseModel):
    picks: list[ReviewPoolPick] = Field(
        default_factory=list,
        description="Picked value streams from the candidate list, in priority order",
    )


__all__ = [
    "InferenceType",
    "ReviewPoolPick",
    "ReviewPoolPickResult",
    "VsClassificationItem",
    "VsClassificationResult",
    "VsVerifierMapping",
    "VsVerifierResult",
]
