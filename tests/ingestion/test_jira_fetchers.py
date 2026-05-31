"""Tests for the ingestion-facing Jira fetch wrappers.

The wrappers are thin delegations over the TicketFetcher port, so the tests use
a recording fake and assert that calls (and their arguments) are forwarded
unchanged. issue_link_reader holds pure helpers and is tested directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from vs_app.ingestion.jira.attachment_fetcher import (
    download_attachment,
    fetch_attachment_contents,
)
from vs_app.ingestion.jira.issue_link_reader import (
    read_issue_links,
    read_linked_issue_keys,
)
from vs_app.ingestion.jira.ticket_fetcher import fetch_ticket_payload, search_tickets


class FakeFetcher:
    """Records calls and returns canned values, mirroring JiraTicketClient."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def get_ticket_data(
        self, ticket_id: str, *, config: Any = None, llm_client: Any = None
    ) -> dict[str, Any]:
        self.calls.append(("get_ticket_data", ticket_id, config, llm_client))
        return {"key": ticket_id}

    async def search_issues(
        self, jql: str, *, start_at: int = 0, max_results: int = 50, config: Any = None
    ) -> dict[str, Any]:
        self.calls.append(("search_issues", jql, start_at, max_results, config))
        return {"jql": jql}

    async def fetch_attachment_content(
        self, attachments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.calls.append(("fetch_attachment_content", attachments))
        return [{"text": "ok"}]

    async def download_attachment(self, attachment: Any, dest_path: str = "") -> Any:
        self.calls.append(("download_attachment", attachment, dest_path))
        return b"bytes"


# ---------------------------------------------------------------------------
# ticket_fetcher
# ---------------------------------------------------------------------------


def test_fetch_ticket_payload_delegates() -> None:
    fake = FakeFetcher()
    config = object()
    llm = object()
    result = asyncio.run(
        fetch_ticket_payload(fake, "IDMT-1", config=config, llm_client=llm)
    )
    assert result == {"key": "IDMT-1"}
    assert fake.calls == [("get_ticket_data", "IDMT-1", config, llm)]


def test_search_tickets_uses_defaults() -> None:
    fake = FakeFetcher()
    result = asyncio.run(search_tickets(fake, "project = IDMT"))
    assert result == {"jql": "project = IDMT"}
    assert fake.calls == [("search_issues", "project = IDMT", 0, 50, None)]


def test_search_tickets_forwards_overrides() -> None:
    fake = FakeFetcher()
    config = object()
    asyncio.run(
        search_tickets(fake, "x", start_at=10, max_results=5, config=config)
    )
    assert fake.calls == [("search_issues", "x", 10, 5, config)]


# ---------------------------------------------------------------------------
# attachment_fetcher
# ---------------------------------------------------------------------------


def test_fetch_attachment_contents_delegates() -> None:
    fake = FakeFetcher()
    attachments = [{"id": "1"}]
    result = asyncio.run(fetch_attachment_contents(fake, attachments))
    assert result == [{"text": "ok"}]
    assert fake.calls == [("fetch_attachment_content", attachments)]


def test_download_attachment_delegates_with_default_dest() -> None:
    fake = FakeFetcher()
    att = {"content": "http://x"}
    result = asyncio.run(download_attachment(fake, att))
    assert result == b"bytes"
    assert fake.calls == [("download_attachment", att, "")]


def test_download_attachment_forwards_dest_path() -> None:
    fake = FakeFetcher()
    asyncio.run(download_attachment(fake, {"id": "1"}, "/tmp/a.pdf"))
    assert fake.calls == [("download_attachment", {"id": "1"}, "/tmp/a.pdf")]


# ---------------------------------------------------------------------------
# issue_link_reader (pure)
# ---------------------------------------------------------------------------


def _issue_with_links(links: list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": "IDMT-1", "fields": {"issuelinks": links}}


def test_read_issue_links_from_full_issue() -> None:
    links = [{"type": {"name": "relates to"}}]
    assert read_issue_links(_issue_with_links(links)) == links


def test_read_issue_links_from_fields_dict() -> None:
    links = [{"type": {"name": "blocks"}}]
    assert read_issue_links({"issuelinks": links}) == links


def test_read_issue_links_missing_returns_empty() -> None:
    assert read_issue_links({"fields": {}}) == []
    assert read_issue_links({"fields": {"issuelinks": None}}) == []


def test_read_linked_issue_keys_outward_and_inward() -> None:
    issue = _issue_with_links(
        [
            {"outwardIssue": {"key": "GROUP-1"}},
            {"inwardIssue": {"key": "EPIC-2"}},
        ]
    )
    assert read_linked_issue_keys(issue) == ["GROUP-1", "EPIC-2"]


def test_read_linked_issue_keys_dedupes_keeping_order() -> None:
    issue = _issue_with_links(
        [
            {"outwardIssue": {"key": "GROUP-1"}},
            {"inwardIssue": {"key": "GROUP-1"}},
            {"outwardIssue": {"key": "EPIC-9"}},
        ]
    )
    assert read_linked_issue_keys(issue) == ["GROUP-1", "EPIC-9"]


def test_read_linked_issue_keys_skips_linkless_entries() -> None:
    issue = _issue_with_links(
        [
            {"type": {"name": "relates to"}},
            {"outwardIssue": {"key": ""}},
            {"outwardIssue": {"key": "EPIC-1"}},
        ]
    )
    assert read_linked_issue_keys(issue) == ["EPIC-1"]
