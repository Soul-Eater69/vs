"""Composition root.

Owns the TicketSourceFactory and RAG/API wiring.
wiring as Phases 4-8 land. Construction lives here (not inside a source package)
so no integration owns another integration's factory.
"""

from __future__ import annotations

from .settings import Settings

VALID_TICKET_SOURCES = ("jira",)


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
    ):
        resolved = normalize_ticket_source(source, default=self.settings.default_ticket_source)
        verify_ssl = self.settings.verify_ssl if verify_ssl is None else verify_ssl

        return self._build_jira(verify_ssl=verify_ssl)

    def _build_jira(self, *, verify_ssl: bool):
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
        )

def build_ticket_fetcher(
    *,
    source: str | None = None,
    verify_ssl: bool = False,
):
    """Backward-compat wrapper matching the legacy `build_ticket_fetcher` signature."""
    factory = TicketSourceFactory()
    return factory.build(source=source, verify_ssl=verify_ssl)
