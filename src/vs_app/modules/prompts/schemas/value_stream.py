"""Pydantic models for value-stream LLM outputs.

Covers three prompts:
  - value_stream_classification.yaml → VsClassificationResult
  - jira_value_stream_verifier.yaml  → VsVerifierResult
  - selection.yaml / rag_selection.yaml → SelectionResult
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


# ---------------------------------------------------------------------------
# selection.yaml / rag_selection.yaml
# ---------------------------------------------------------------------------

class SelectedValueStream(BaseModel):
    entity_id: str = Field(description="The entity ID of the value stream")
    entity_name: str = Field(description="The name of the value stream")
    confidence: float = Field(
        description="0.8-1.0 strong direct alignment, 0.5-0.7 partial, 0.3-0.4 weak but plausible"
    )
    reason: str = Field(
        description=(
            "Brief business rationale: explain why the idea card maps to this value stream. "
            "Do not mention retrieval score, rank, support count, similarity, or lane name."
        )
    )


class SelectionResult(BaseModel):
    selected_value_streams: list[SelectedValueStream] = Field(
        default_factory=list,
        description="Value streams that are relevant to the idea card",
    )


__all__ = [
    "InferenceType",
    "SelectedValueStream",
    "SelectionResult",
    "VsClassificationItem",
    "VsClassificationResult",
    "VsVerifierMapping",
    "VsVerifierResult",
]
