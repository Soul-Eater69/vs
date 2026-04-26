"""Pydantic models for ticket summary LLM output.

Mirrors the schema declared in `prompt_yaml/retrieval_summary.yaml`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SummaryOutput(BaseModel):
    """Structured output of the retrieval_summary prompt."""

    summary_text: str = ""
    business_problem: str = ""
    business_capability: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    systems_and_products: list[str] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)


__all__ = ["SummaryOutput"]
