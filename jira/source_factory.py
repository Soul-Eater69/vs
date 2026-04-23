"""Factory helpers for choosing the ticket-fetch source at runtime."""

from __future__ import annotations

import os
from typing import Any

from .ticket_client import JiraTicketClient, TicketFetcher

VALID_TICKET_SOURCES = ("jira", "neo4j")


def normalize_ticket_source(source: str | None = None) -> str:
    resolved = str(
        source or os.environ.get("INGESTION_TICKET_SOURCE", "jira")
    ).strip().lower()
    if resolved not in VALID_TICKET_SOURCES:
        allowed = ", ".join(VALID_TICKET_SOURCES)
        raise ValueError(f"Unknown ticket source {resolved!r}. Expected one of: {allowed}")
    return resolved


def build_ticket_fetcher(
    *,
    source: str | None = None,
    verify_ssl: bool = False,
    sharepoint_client: Any = None,
) -> TicketFetcher:
    """Create the configured ticket-fetch client.

    When ``source='neo4j'``, Jira auth is still passed through for attachment
    downloads if ``JIRA_TOKEN`` is available in the environment.
    """
    resolved = normalize_ticket_source(source)

    if resolved == "jira":
        from config import JIRA_BASE_URL, JIRA_TOKEN

        if not JIRA_BASE_URL or not JIRA_TOKEN:
            raise RuntimeError("JIRA_BASE_URL and JIRA_TOKEN must be set for Jira ticket ingestion")

        return JiraTicketClient(
            base_url=JIRA_BASE_URL,
            token=JIRA_TOKEN,
            verify_ssl=verify_ssl,
            sharepoint_client=sharepoint_client,
        )

    from config import JIRA_TOKEN, NEO4J_AUTH, NEO4J_DATABASE, NEO4J_URI
    from .neo4j_ticket_client import Neo4jTicketClient

    if not NEO4J_URI:
        raise RuntimeError("NEO4J_URI must be set for Neo4j ticket ingestion")

    return Neo4jTicketClient(
        uri=NEO4J_URI,
        auth=NEO4J_AUTH,
        database=NEO4J_DATABASE,
        attachment_auth_token=JIRA_TOKEN,
        verify_ssl=verify_ssl,
        sharepoint_client=sharepoint_client,
    )

