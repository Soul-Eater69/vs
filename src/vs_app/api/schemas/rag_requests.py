from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class ValueStreamRagRequest(BaseModel):
    ticket_id: str | None = None
    idea_card_text: str | None = None
    source: Literal["jira"] | None = None
    top_k_historical: int = 20
    top_k_value_streams: int = 20
    use_llm_finalizer: bool = True
    exclude_source_ticket_from_historical: bool = True

    @model_validator(mode="after")
    def validate_query_input(self) -> "ValueStreamRagRequest":
        if self.ticket_id or self.idea_card_text:
            return self
        raise ValueError("At least one of ticket_id or idea_card_text must be provided.")
