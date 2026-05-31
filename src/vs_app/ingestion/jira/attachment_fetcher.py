"""Ingestion-facing Jira attachment fetch helpers.

Thin wrappers over the low-level Jira client (the ``TicketFetcher`` port
implemented by ``vs_app.integrations.jira.client.JiraTicketClient``). They give
ingestion code clear, domain-named entry points for downloading attachment
content without reaching into the transport layer.

Fetchers only: text extraction, ranking, and consolidation live in the
``extraction`` package, not here. The ``fetcher`` argument is any object
implementing the ``TicketFetcher`` port (kept as ``Any`` so test fakes work).
"""

from __future__ import annotations

from typing import Any


async def fetch_attachment_contents(
    fetcher: Any,
    attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Download and read content for the given Jira attachments."""
    return await fetcher.fetch_attachment_content(attachments)


async def download_attachment(
    fetcher: Any,
    attachment: Any,
    dest_path: str = "",
) -> Any:
    """Download a single Jira attachment to ``dest_path`` (or return content)."""
    return await fetcher.download_attachment(attachment, dest_path)


__all__ = ["fetch_attachment_contents", "download_attachment"]
