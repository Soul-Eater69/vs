"""Compatibility shim for legacy src Jira client path.

Canonical implementation lives at:
    jira.value_stream_client
"""

from jira.value_stream_client import JiraValueStreamClient

__all__ = ["JiraValueStreamClient"]