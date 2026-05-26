from __future__ import annotations

import pytest

from vs_app.ingestion.jira.mapper import build_ticket_payload
from vs_app.ingestion.summary import text_consolidator
from vs_app.ingestion.summary.text_consolidator import consolidate_ticket_text


class Cfg:
    max_documents = 2
    max_prefetch_attachment_size = 60_000_000


class SmallCfg(Cfg):
    max_prefetch_attachment_size = 100


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
        lambda file_bytes, att, cfg, progress=None: " ".join(["useful"] * 40),
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


@pytest.mark.anyio
async def test_jira_comments_are_not_added_to_consolidated_text() -> None:
    ticket = {
        "key": "IDMT-1",
        "fields": {
            "description": "Description body",
            "comment": {
                "comments": [
                    {
                        "author": {"displayName": "Analyst"},
                        "body": "This Jira comment should not be ingested.",
                    }
                ]
            },
        },
        "attachments": [],
    }

    text = await consolidate_ticket_text(ticket, JiraClient(), Cfg())

    assert "[DESCRIPTION]\nDescription body" in text
    assert "[COMMENT" not in text
    assert "This Jira comment should not be ingested." not in text


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


@pytest.mark.anyio
async def test_attachment_progress_messages(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: " ".join(["useful"] * 40),
    )
    client = JiraClient()
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [
            {"id": "1", "filename": "Idea Card.pptx", "size": 123},
            {"id": "2", "filename": "image.png", "size": 5},
        ],
    }

    await consolidate_ticket_text(ticket, client, Cfg(), progress=messages.append)

    assert "ATTACHMENTS found=2 supported_docs=1 max_documents=2" in messages
    assert "ATTACHMENT skipped unsupported: image.png" in messages
    assert "ATTACHMENT attempting 1/1: Idea Card.pptx size=123" in messages
    assert "ATTACHMENT accepted 1/2: Idea Card.pptx words=40 chars=279" in messages


@pytest.mark.anyio
async def test_attachment_progress_reports_too_large(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: " ".join(["useful"] * 40),
    )
    client = JiraClient()
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [
            {"id": "1", "filename": "LargeDeck.pptx", "size": 101},
        ],
    }

    text = await consolidate_ticket_text(ticket, client, SmallCfg(), progress=messages.append)

    assert "ATTACHMENT skipped too_large: LargeDeck.pptx size=101 max=100" in messages
    assert client.downloaded == []
    assert "[DOCUMENT: LargeDeck.pptx]" not in text


@pytest.mark.anyio
async def test_five_word_attachment_text_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: "one two three four five",
    )
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [{"id": "1", "filename": "Idea Card.pdf", "size": 10}],
    }

    text = await consolidate_ticket_text(ticket, JiraClient(), Cfg(), progress=messages.append)

    assert "ATTACHMENT skipped weak_text: Idea Card.pdf reason=too_few_words<30 words=5 chars=23" in messages
    assert "[DOCUMENT: Idea Card.pdf]" not in text


@pytest.mark.anyio
async def test_empty_attachment_text_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: "",
    )
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [{"id": "1", "filename": "Idea Card.pdf", "size": 10}],
    }

    text = await consolidate_ticket_text(ticket, JiraClient(), Cfg(), progress=messages.append)

    assert "ATTACHMENT skipped weak_text: Idea Card.pdf reason=empty words=0 chars=0" in messages
    assert "[DOCUMENT: Idea Card.pdf]" not in text


@pytest.mark.anyio
async def test_hundred_word_attachment_text_is_accepted(monkeypatch) -> None:
    extracted = " ".join(f"word{i}" for i in range(100))
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: extracted,
    )
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [{"id": "1", "filename": "Idea Card.pdf", "size": 10}],
    }

    text = await consolidate_ticket_text(ticket, JiraClient(), Cfg(), progress=messages.append)

    assert f"ATTACHMENT accepted 1/2: Idea Card.pdf words=100 chars={len(extracted)}" in messages
    assert "[DOCUMENT: Idea Card.pdf]" in text


@pytest.mark.anyio
async def test_eight_thousand_word_attachment_text_is_accepted(monkeypatch) -> None:
    extracted = " ".join(f"word{i}" for i in range(8000))
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: extracted,
    )
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [{"id": "1", "filename": "Idea Card.pdf", "size": 10}],
    }

    text = await consolidate_ticket_text(ticket, JiraClient(), Cfg(), progress=messages.append)

    assert f"ATTACHMENT accepted 1/2: Idea Card.pdf words=8000 chars={len(extracted)}" in messages
    assert "[DOCUMENT: Idea Card.pdf]" in text


@pytest.mark.anyio
async def test_document_budget_counts_accepted_documents_not_attempts(monkeypatch) -> None:
    def fake_extract(file_bytes, att, cfg, progress=None):
        if att["filename"] == "Idea Card.pdf":
            return "too few words"
        return " ".join(["useful"] * 40)

    monkeypatch.setattr(text_consolidator, "_extract_bytes_to_text", fake_extract)
    client = JiraClient()
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [
            {"id": "1", "filename": "Idea Card.pdf", "size": 10},
            {"id": "2", "filename": "Proposal.docx", "size": 10},
            {"id": "3", "filename": "Random.pdf", "size": 10},
        ],
    }

    text = await consolidate_ticket_text(ticket, client, Cfg(), progress=messages.append)

    assert "[DOCUMENT: Idea Card.pdf]" not in text
    assert "[DOCUMENT: Proposal.docx]" in text
    assert "[DOCUMENT: Random.pdf]" in text
    assert "ATTACHMENT accepted 1/2: Proposal.docx words=40 chars=279" in messages
    assert "ATTACHMENT accepted 2/2: Random.pdf words=40 chars=279" in messages


@pytest.mark.anyio
async def test_first_document_extraction_error_continues_to_next_document(monkeypatch) -> None:
    def fake_extract(file_bytes, att, cfg, progress=None):
        if att["filename"] == "Idea Card.pdf":
            raise RuntimeError("bad pdf")
        return " ".join(["useful"] * 40)

    monkeypatch.setattr(text_consolidator, "_extract_bytes_to_text", fake_extract)
    client = JiraClient()
    messages: list[str] = []
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [
            {"id": "1", "filename": "Idea Card.pdf", "size": 10},
            {"id": "2", "filename": "Business Case.pdf", "size": 20},
        ],
    }

    text = await consolidate_ticket_text(ticket, client, Cfg(), progress=messages.append)

    assert "[DOCUMENT: Idea Card.pdf]" not in text
    assert "[DOCUMENT: Business Case.pdf]" in text
    assert "ATTACHMENT error extracting: Idea Card.pdf error=bad pdf" in messages
    assert "ATTACHMENT accepted 1/2: Business Case.pdf words=40 chars=279" in messages


@pytest.mark.anyio
async def test_idea_card_filename_ranks_first(monkeypatch) -> None:
    monkeypatch.setattr(
        text_consolidator,
        "_extract_bytes_to_text",
        lambda file_bytes, att, cfg, progress=None: " ".join(["useful"] * 40),
    )
    client = JiraClient()
    ticket = {
        "key": "IDMT-1",
        "fields": {},
        "attachments": [
            {"id": "1", "filename": "Business Case.pptx", "size": 1},
            {"id": "2", "filename": "Idea Card.docx", "size": 99},
        ],
    }

    await consolidate_ticket_text(ticket, client, Cfg())

    assert client.downloaded[:2] == ["Idea Card.docx", "Business Case.pptx"]
