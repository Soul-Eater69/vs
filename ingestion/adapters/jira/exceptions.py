"""Backward-compat shim. Canonical module: vs_app.integrations.jira.exceptions."""

from vs_app.integrations.jira.exceptions import *  # noqa: F401,F403
from vs_app.integrations.jira.exceptions import (  # noqa: F401
    JIRAApiError,
    JIRAAuthenticationError,
    JIRAError,
)
