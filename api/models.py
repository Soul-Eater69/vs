from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GenerateRequest(BaseModel):
    doc_id: Optional[str] = None
    query_text: Optional[str] = None
    fetch_count: int = 12
    approach: str = "plain"  # "plain" | "historic-rag"
    allowed_value_stream_names: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = 15


class ComparisonRequest(BaseModel):
    predicted: list[str]
    ground_truth: list[str]
    fuzzy_threshold: float = 0.75
