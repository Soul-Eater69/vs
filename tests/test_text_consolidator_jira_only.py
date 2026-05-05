from __future__ import annotations

import pytest

from vs_app.ingestion.jira.mapper import build_ticket_payload
from vs_app.ingestion.summary import text_consolidator
from vs_app.ingestion.summary.text_consolidator import consolidate_ticket_text


class Cfg:
    max_documents = 2
    max_prefetch_attachment_size = 60_000_000


class JiraClient:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    async def download_attachment(self, att):
        self.downloaded.append(att["filename"])
        return b"bytes"


@pytest.mark.anyio
async def test_only_max_documents_downloaded_and_ordered(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg: " ".join(["useful"] * 40),
    )
    client = JiraClient()
    ticket = {
        "key": "IDMT-1",
        "fields": {"description": "Description body"},
        "attachments": [
            {"id": "1", "filename": "random.pdf", "size": 100},
            {"id": "2", "filename": "idea card.pdf", "size": 200},
            {"id": "3", "filename": "notes.txt", "size": 1},
            {"id": "4", "filename": "proposal.docx", "size": 50},
        ],
    }

    text = await consolidate_ticket_text(ticket, client, Cfg())

    assert client.downloaded == ["idea card.pdf", "proposal.docx"]
    assert "[DOCUMENT: idea card.pdf]" in text
    assert "[DOCUMENT: proposal.docx]" in text
    assert "notes.txt" not in text
    assert "LINKED DOCUMENT" not in text
    assert "SharePoint" not in text


def test_description_links_are_not_converted_into_attachments() -> None:
    issue = {
        "key": "IDMT-1",
        "fields": {
            "description": "See https://example.com/idea-card.pdf",
            "attachment": [{"id": "1", "filename": "jira.pdf"}],
            "issuelinks": [],
        },
    }

    payload = build_ticket_payload(issue, ticket_id="IDMT-1")

    assert payload["attachments"] == [{"id": "1", "filename": "jira.pdf"}]
    assert "description_attachments" not in payload
