"""Backward-compat shim. Canonical module: vs_app.integrations.jira.client."""

from vs_app.integrations.jira.client import *  # noqa: F401,F403
from vs_app.integrations.jira.client import (  # noqa: F401
    JIRARestClient,
    JiraTicketClient,
)
from vs_app.integrations.jira.mapper import merge_attachments as _merge_attachments  # noqa: F401
