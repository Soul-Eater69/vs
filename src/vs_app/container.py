"""Composition root.

Owns the TicketSourceFactory and will grow to own IngestionService / RagImpactService
wiring as Phases 4-8 land. Construction lives here (not inside a source package)
so no integration owns another integration's factory.
"""

from __future__ import annotations

from typing import Any

from .settings import Settings

VALID_TICKET_SOURCES = ("jira", "neo4j")


def normalize_ticket_source(source: str | None = None, *, default: str = "jira") -> str:
    resolved = (source or default or "").strip().lower()
    if resolved not in VALID_TICKET_SOURCES:
        allowed = ", ".join(VALID_TICKET_SOURCES)
        raise ValueError(f"Unknown ticket source {resolved!r}. Expected one of: {allowed}")
    return resolved


class TicketSourceFactory:
    """Builds a ticket-fetch client for the configured source.

    Holds env/config state so integration classes stay source-specific only.
    Returned clients already implement the TicketFetcher port contract.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()

    def build(
        self,
        source: str | None = None,
        *,
        verify_ssl: bool | None = None,
        sharepoint_client: Any = None,
    ):
        resolved = normalize_ticket_source(source, default=self.settings.default_ticket_source)
        verify_ssl = self.settings.verify_ssl if verify_ssl is None else verify_ssl

        if resolved == "jira":
            return self._build_jira(verify_ssl=verify_ssl, sharepoint_client=sharepoint_client)
        return self._build_neo4j(verify_ssl=verify_ssl, sharepoint_client=sharepoint_client)

    def _build_jira(self, *, verify_ssl: bool, sharepoint_client: Any):
        # Import here to avoid coupling the container to Jira at module load.
        from vs_app.integrations.jira.client import JiraTicketClient

        if not self.settings.jira_base_url or not self.settings.jira_token:
            raise RuntimeError(
                "JIRA_BASE_URL and JIRA_TOKEN must be set for Jira ticket ingestion"
            )
        return JiraTicketClient(
            base_url=self.settings.jira_base_url,
            token=self.settings.jira_token,
            verify_ssl=verify_ssl,
            sharepoint_client=sharepoint_client,
        )

    def _build_neo4j(self, *, verify_ssl: bool, sharepoint_client: Any):
        # Import here to avoid coupling the container to Neo4j at module load.
        from vs_app.integrations.neo4j.ticket_source import Neo4jTicketClient

        if not self.settings.neo4j_uri:
            raise RuntimeError("NEO4J_URI must be set for Neo4j ticket ingestion")
        return Neo4jTicketClient(
            uri=self.settings.neo4j_uri,
            auth=(self.settings.neo4j_user, self.settings.neo4j_password),
            database=self.settings.neo4j_database,
            ticket_label=self.settings.neo4j_ticket_label,
            ticket_key_property=self.settings.neo4j_ticket_key_property,
            group_link_field=self.settings.neo4j_group_link_field,
            issue_type_property=self.settings.neo4j_issue_type_property,
            attachment_auth_token=self.settings.jira_token,
            verify_ssl=verify_ssl,
            sharepoint_client=sharepoint_client,
        )


def build_ticket_fetcher(
    *,
    source: str | None = None,
    verify_ssl: bool = False,
    sharepoint_client: Any = None,
):
    """Backward-compat wrapper matching the legacy `build_ticket_fetcher` signature."""
    factory = TicketSourceFactory()
    return factory.build(source=source, verify_ssl=verify_ssl, sharepoint_client=sharepoint_client)
