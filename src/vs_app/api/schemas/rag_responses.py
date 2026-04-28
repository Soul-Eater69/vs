from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ValueStreamRagResponse(BaseModel):
    selected_value_streams: list[dict] = Field(default_factory=list)
    auto_selected_value_streams: list[dict] = Field(default_factory=list)
    llm_selected_value_streams: list[dict] = Field(default_factory=list)
    rescued_historical_gap_fill_value_streams: list[dict] = Field(default_factory=list)
    dropped_historical_gap_fill_value_streams: list[dict] = Field(default_factory=list)
    rejected_candidates: list[dict] = Field(default_factory=list)
    semantic_candidate_value_streams: list[dict] = Field(default_factory=list)
    historical_candidate_value_streams: list[dict] = Field(default_factory=list)
    merged_candidate_value_streams: list[dict] = Field(default_factory=list)
    historical_ticket_hits: list[dict] = Field(default_factory=list)
    historical_value_stream_support: list[dict] = Field(default_factory=list)
    candidate_value_streams: list[dict] = Field(default_factory=list)
    llm_candidates: list[dict] = Field(default_factory=list)
    historical_source: str = ""
    raw_response: Any = None
    query_preparation: dict[str, Any] = Field(default_factory=dict)
    warnings: list[Any] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)
    ground_truth: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: object) -> "ValueStreamRagResponse":
        return cls(
            selected_value_streams=list(getattr(result, "selected_value_streams", []) or []),
            auto_selected_value_streams=list(getattr(result, "auto_selected_value_streams", []) or []),
            llm_selected_value_streams=list(getattr(result, "llm_selected_value_streams", []) or []),
            rescued_historical_gap_fill_value_streams=list(
                getattr(result, "rescued_historical_gap_fill_value_streams", []) or []
            ),
            dropped_historical_gap_fill_value_streams=list(
                getattr(result, "dropped_historical_gap_fill_value_streams", []) or []
            ),
            rejected_candidates=list(getattr(result, "rejected_candidates", []) or []),
            semantic_candidate_value_streams=list(
                getattr(result, "semantic_candidate_value_streams", []) or []
            ),
            historical_candidate_value_streams=list(
                getattr(result, "historical_candidate_value_streams", []) or []
            ),
            merged_candidate_value_streams=list(
                getattr(result, "merged_candidate_value_streams", []) or []
            ),
            historical_ticket_hits=list(getattr(result, "historical_ticket_hits", []) or []),
            historical_value_stream_support=list(
                getattr(result, "historical_value_stream_support", []) or []
            ),
            candidate_value_streams=list(getattr(result, "candidate_value_streams", []) or []),
            llm_candidates=list(getattr(result, "llm_candidates", []) or []),
            historical_source=str(getattr(result, "historical_source", "") or ""),
            raw_response=getattr(result, "raw_response", None),
            query_preparation=dict(getattr(result, "query_preparation", {}) or {}),
            warnings=list(getattr(result, "warnings", []) or []),
            evidence=list(getattr(result, "evidence", []) or []),
            debug=dict(getattr(result, "debug", {}) or {}),
        )
