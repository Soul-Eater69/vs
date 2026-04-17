from .ticket_client import (
    JiraTicketClient,
    JiraValueStreamClient,
    TicketFetcher,
    ValueStreamFetcher,
)
from typing import Any


class JiraTicketExtractionService:
    """Thin adapter over Jira client fetch API.

    Keeps Jira-fetch responsibility in jira module.
    """

    def __init__(self, jira_client: Any) -> None:
        self._jira_client = jira_client

    async def extract_ticket(self, ticket_id: str, cfg: Any) -> dict:
        return await self._jira_client.get_ticket_data(ticket_id, config=cfg)

__all__ = [
    "JiraTicketClient",
    "JiraValueStreamClient",
    "JiraTicketExtractionService",
    "TicketFetcher",
    "ValueStreamFetcher",
]
