"""Pydantic models for stage-support LLM output.

  - stage_support_classification.yaml → StageSupportResult

Fields are optional with defaults so a partial item from the gateway does not
raise; the stage-support classifier validates value stream / stage names against
the supplied Jira GT and drops anything invented or mislabelled.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class StageSupportItem(BaseModel):
    value_stream_name: str = ""
    stage_name: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""
    confidence: Optional[float] = None


class StageSupportResult(BaseModel):
    """Output of the stage_support_classification prompt."""

    stages: list[StageSupportItem] = Field(default_factory=list)


__all__ = ["StageSupportItem", "StageSupportResult"]
