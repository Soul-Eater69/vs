"""Application service: TicketExtractionService facade."""

from __future__ import annotations

from typing import Any


class TicketExtractionService:
    """Facade over any TicketFetcher — bridges service-layer use cases to the port."""

    def __init__(self, ticket_client: Any | None = None, jira_client: Any | None = None) -> None:
        self._ticket_client = ticket_client if ticket_client is not None else jira_client
        if self._ticket_client is None:
            raise ValueError("A ticket client is required")

    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        from ..adapters.jira.fetch_compat import get_ticket_data_compat
        return await get_ticket_data_compat(self._ticket_client, ticket_id, config=cfg)
