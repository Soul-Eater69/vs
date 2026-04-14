from __future__ import annotations

from typing import Any, Protocol


class TicketExtractionPort(Protocol):
    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        ...


class TicketProcessingPort(Protocol):
    async def process_ticket(self, ticket_data: dict, *, cfg: Any, deps: Any) -> dict:
        ...