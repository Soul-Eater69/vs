"""Ingestion-facing Jira ticket fetch helpers.

Thin wrappers over the low-level Jira client (the ``TicketFetcher`` port
implemented by ``vs_app.integrations.jira.client.JiraTicketClient``). They give
ingestion code clear, domain-named entry points without reaching into the
transport layer.

These are fetchers only: no summarisation, classification, Azure document
building, or prediction. The ``fetcher`` argument is any object implementing the
``TicketFetcher`` port (kept as ``Any`` so test fakes work without importing the
transport layer).
"""

from __future__ import annotations

from typing import Any


async def fetch_ticket_payload(
    fetcher: Any,
    ticket_id: str,
    *,
    config: Any | None = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    """Fetch the canonical ingestion payload for a single IDMT ticket."""
    return await fetcher.get_ticket_data(
        ticket_id, config=config, llm_client=llm_client
    )


async def search_tickets(
    fetcher: Any,
    jql: str,
    *,
    start_at: int = 0,
    max_results: int = 50,
    config: Any | None = None,
) -> dict[str, Any]:
    """Search Jira issues by JQL, returning the raw search response."""
    return await fetcher.search_issues(
        jql, start_at=start_at, max_results=max_results, config=config
    )


__all__ = ["fetch_ticket_payload", "search_tickets"]
