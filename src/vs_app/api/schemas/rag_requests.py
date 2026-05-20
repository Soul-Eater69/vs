from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValueStreamRagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str | None = None
    idea_card_text: str | None = None
    source_ticket_title: str | None = None
    semantic_fetch_k: int = 60
    historical_ticket_fetch_k: int = 6
    llm_candidate_window: int = 36
    final_output_count: int = 12
    exclude_source_ticket_from_historical: bool = True
    include_stage_predictions: bool = False
    extraction_backend: Literal["current", "unstructured", "auto"] = "current"
    extraction_debug: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_query_input(self) -> "ValueStreamRagRequest":
        if not (self.ticket_id or self.idea_card_text):
            raise ValueError("At least one of ticket_id or idea_card_text must be provided.")
        requested = 12 if self.final_output_count is None else int(self.final_output_count)
        self.final_output_count = min(25, max(1, requested))
        return self
