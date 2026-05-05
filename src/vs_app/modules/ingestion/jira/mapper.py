"""Jira API issue -> ticket payload dict.

Pure functions that transform a raw Jira issue JSON into the dict shape that
the ingestion pipeline has always consumed. No REST calls, no side effects.

Output shape is fixed and must not change — ingestion downstream depends on it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from vs_app.modules.ingestion.jira.value_stream_labels.theme_extraction import (
    extract_themes,
    resolve_value_streams,
)


def build_ticket_payload(
    issue: Dict[str, Any],
    *,
    ticket_id: str,
    config: Optional[Any] = None,
    llm_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Assemble the canonical ticket payload dict from a Jira issue JSON.

    Mirrors the legacy `JiraTicketClient.get_ticket_data` return shape exactly.
    """
    fields = issue.get("fields", {})

    attachments = list(fields.get("attachment", []) or [])

    issuelinks = fields.get("issuelinks", [])
    themes = extract_themes(issuelinks)
    vs_data = resolve_value_streams(themes, issuelinks, llm_client=llm_client)

    return {
        "key": issue.get("key", ticket_id),
        "fields": fields,
        "attachments": attachments,
        "themes": themes,
        **vs_data,
    }
