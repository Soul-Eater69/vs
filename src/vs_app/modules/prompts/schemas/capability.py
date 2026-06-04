"""Pydantic models for L2/L3 capability LLM outputs.

Covers two prompts:
  - capability_l2_generation.yaml -> L2CapabilityResult
  - capability_l3_generation.yaml -> L3CapabilityResult

Fields are optional with defaults so a partial/empty structured response from the
gateway does not raise; the generator drops empty rows and stays lenient.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class L2CapabilityItem(BaseModel):
    capability_name: str = ""
    rationale: str = ""
    confidence: float = 0.0


class L2CapabilityResult(BaseModel):
    capabilities: list[L2CapabilityItem] = Field(default_factory=list)


class L3CapabilityItem(BaseModel):
    capability_name: str = ""
    parent_l2_capability_name: str = ""
    rationale: str = ""
    confidence: float = 0.0


class L3CapabilityResult(BaseModel):
    capabilities: list[L3CapabilityItem] = Field(default_factory=list)


__all__ = [
    "L2CapabilityItem",
    "L2CapabilityResult",
    "L3CapabilityItem",
    "L3CapabilityResult",
]
