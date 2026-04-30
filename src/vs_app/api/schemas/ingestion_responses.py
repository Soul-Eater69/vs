from __future__ import annotations

from pydantic import BaseModel, Field


class IngestTicketResponse(BaseModel):
    ticket_id: str
    summary: dict | None = None
    errors: list[str] = Field(default_factory=list)

    @classmethod
    def from_result(cls, result: object) -> "IngestTicketResponse":
        return cls(
            ticket_id=str(getattr(result, "ticket_id")),
            summary=getattr(result, "summary", None),
            errors=list(getattr(result, "errors", []) or []),
        )
