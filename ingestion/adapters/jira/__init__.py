"""Backward-compat shim. Canonical package: vs_app.integrations.jira."""

from vs_app.container import build_ticket_fetcher, normalize_ticket_source  # noqa: F401
from vs_app.integrations.jira import (  # noqa: F401
    JIRAApiError,
    JIRAAuthenticationError,
    JIRAError,
    JIRAQueryBuilder,
    JIRARestClient,
    JiraTicketClient,
    build_ticket_payload,
    get_ticket_data_compat,
    jira_field_provider,
    merge_attachments,
)

__all__ = [
    "JIRAApiError",
    "JIRAAuthenticationError",
    "JIRAError",
    "JIRAQueryBuilder",
    "JIRARestClient",
    "JiraTicketClient",
    "build_ticket_fetcher",
    "build_ticket_payload",
    "get_ticket_data_compat",
    "jira_field_provider",
    "merge_attachments",
    "normalize_ticket_source",
]
