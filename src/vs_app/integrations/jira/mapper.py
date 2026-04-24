"""Jira API issue -> ticket payload dict.

Pure functions that transform a raw Jira issue JSON into the dict shape that
the ingestion pipeline has always consumed. No REST calls, no side effects.

Output shape is fixed and must not change — ingestion downstream depends on it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from vs_app.integrations.files.description_links import extract_description_link_attachments
from vs_app.modules.ingestion.value_stream_labels.epic_extraction import (
    extract_epics,
    map_value_streams_to_epics,
)
from vs_app.modules.ingestion.value_stream_labels.theme_extraction import (
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

    attachments = fields.get("attachment", [])
    description_attachments = extract_description_link_attachments(fields.get("description"))
    merged_attachments = merge_attachments(attachments, description_attachments)

    issuelinks = fields.get("issuelinks", [])
    themes = extract_themes(issuelinks)
    vs_data = resolve_value_streams(themes, issuelinks, llm_client=llm_client)

    epics = extract_epics(issue, config=config)
    value_stream_epics = map_value_streams_to_epics(vs_data["linked_value_streams"], epics)

    return {
        "key": issue.get("key", ticket_id),
        "fields": fields,
        "attachments": merged_attachments,
        "description_attachments": description_attachments,
        "themes": themes,
        "epics": epics,
        **vs_data,
        "value_stream_epics": value_stream_epics,
    }


def merge_attachments(
    api_attachments: List[Dict[str, Any]],
    description_attachments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge API attachments with description-embedded link attachments, de-duped by filename."""
    existing_keys: set[str] = set()
    for att in api_attachments:
        existing_keys.add(str(att.get("filename") or "").strip().lower())

    merged = list(api_attachments)
    for att in description_attachments:
        key = (
            str(att.get("content") or "").strip().lower(),
            str(att.get("filename") or "").strip().lower(),
        )
        if key[1] in existing_keys:
            continue
        merged.append(att)
        existing_keys.add(key[1])

    return merged
