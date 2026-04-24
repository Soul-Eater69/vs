from __future__ import annotations

from typing import Any


class JIRAError(Exception):
    """Base class for Jira client errors."""


class JIRAApiError(JIRAError):
    """Raised when a Jira API request fails."""

    def __init__(self, status_code: int, detail: str = "", payload: Any | None = None) -> None:
        super().__init__(detail or f"Jira API request failed with status {status_code}")
        self.status_code = status_code
        self.detail = detail
        self.payload = payload


class JIRAAuthenticationError(JIRAApiError):
    """Raised when Jira authentication fails."""

    def __init__(self, detail: str = "Error connecting to JIRA", payload: Any | None = None) -> None:
        super().__init__(status_code=401, detail=detail, payload=payload)
