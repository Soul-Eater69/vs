from .ticket_client import (
    JiraTicketClient,
    JiraValueStreamClient,
    TicketFetcher,
    ValueStreamFetcher,
)
from .neo4j_ticket_client import Neo4jTicketClient
from .source_factory import build_ticket_fetcher, normalize_ticket_source
from typing import Any


class TicketExtractionService:
    """Thin adapter over the ticket-fetch API.

    Keeps source-specific fetch responsibility in the source client.
    """

    def __init__(self, ticket_client: Any | None = None, jira_client: Any | None = None) -> None:
        self._ticket_client = ticket_client if ticket_client is not None else jira_client
        if self._ticket_client is None:
            raise ValueError("A ticket client is required")

    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        return await self._ticket_client.get_ticket_data(ticket_id, config=cfg)


JiraTicketExtractionService = TicketExtractionService

__all__ = [
    "JiraTicketClient",
    "JiraValueStreamClient",
    "Neo4jTicketClient",
    "TicketExtractionService",
    "JiraTicketExtractionService",
    "TicketFetcher",
    "ValueStreamFetcher",
    "build_ticket_fetcher",
    "normalize_ticket_source",
]
