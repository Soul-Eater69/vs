from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class ValueStreamRagRequest(BaseModel):
    ticket_id: str | None = None
    idea_card_text: str | None = None
    source: Literal["jira"] | None = None
    top_k_historical: int = 20
    top_k_value_streams: int = 20
    semantic_fetch_k: int = 40
    historical_ticket_fetch_k: int = 35
    llm_candidate_window: int = 30
    final_output_count: int = 12
    use_llm_finalizer: bool = True
    exclude_source_ticket_from_historical: bool = True

    @model_validator(mode="after")
    def validate_query_input(self) -> "ValueStreamRagRequest":
        if not (self.ticket_id or self.idea_card_text):
            raise ValueError("At least one of ticket_id or idea_card_text must be provided.")
        requested = 12 if self.final_output_count is None else int(self.final_output_count)
        self.final_output_count = min(25, max(1, requested))
        return self
