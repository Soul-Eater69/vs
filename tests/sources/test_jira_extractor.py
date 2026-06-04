"""Fake-only tests for the reusable Jira source extractor.

No live Jira client construction, no network, no Azure/LLM. A fake duck-typed
client supplies canned issue / linked-issue / attachment / epic data.
"""

from __future__ import annotations

from vs_app.sources.jira import (
    ExtractedIDMTRecord,
    extract_idmt_record,
)
from vs_app.sources.jira.attachments import (
    combine_attachment_texts,
    normalize_attachment_metadata,
)


class FakeJiraClient:
    """Duck-typed client returning canned data; records no network calls."""

    def __init__(
        self,
        *,
        issue=None,
        linked=None,
        attachments=None,
        attachment_texts=None,
        child_epics=None,
    ) -> None:
        self._issue = issue or {}
        self._linked = linked or []
        self._attachments = attachments or []
        self._attachment_texts = attachment_texts or {}
        self._child_epics = child_epics or {}

    def get_issue(self, ticket_id):
        return self._issue

    def get_linked_issues(self, ticket_id):
        return self._linked

    def get_attachments(self, ticket_id):
        return self._attachments

    def get_attachment_text(self, attachment):
        return self._attachment_texts.get(attachment.get("id"), "")

    def get_child_epics(self, group_id):
        return self._child_epics.get(group_id, [])


def _full_client() -> FakeJiraClient:
    return FakeJiraClient(
        issue={
            "key": "IDMT-2099",
            "fields": {
                "summary": "Improve Prior Authorization",
                "description": "We need faster prior auth handling.",
                "idea_card_text": "Idea: automate prior auth intake.",
            },
        },
        linked=[
            {
                "group_id": "GROUP-1",
                "summary": "Prior Auth Theme",
                "business_needs": "Reduce manual auth review.",
                "value_stream_id": "VS-UM",
                "value_stream_name": "Manage Utilization Management Program",
            }
        ],
        attachments=[
            {"id": "att1", "filename": "spec.pdf", "mimeType": "application/pdf", "size": 1024, "content": "http://x/att1"},
            {"id": "att2", "filename": "notes.txt", "mime_type": "text/plain"},
        ],
        attachment_texts={"att1": "Spec body text.", "att2": "Notes body text."},
        child_epics={
            "GROUP-1": [
                {"epic_id": "EPIC-10", "summary": "Auth intake", "status": "Open", "source": "jira"},
                {"key": "EPIC-11", "summary": "Auth review"},
            ]
        },
    )


def test_extracts_basic_idmt_fields() -> None:
    record = extract_idmt_record(ticket_id="IDMT-2099", client=_full_client())
    assert isinstance(record, ExtractedIDMTRecord)
    assert record.ticket_id == "IDMT-2099"
    assert record.title == "Improve Prior Authorization"
    assert record.summary == "Improve Prior Authorization"
    assert record.description == "We need faster prior auth handling."
    assert record.idea_card_text == "Idea: automate prior auth intake."


def test_extracted_text_combines_idea_card_and_attachment_text() -> None:
    record = extract_idmt_record(ticket_id="IDMT-2099", client=_full_client())
    assert "Spec body text." in record.attachment_text
    assert "Notes body text." in record.attachment_text
    # extracted_text = idea card + attachment text
    assert "Idea: automate prior auth intake." in record.extracted_text
    assert "Spec body text." in record.extracted_text


def test_extracts_linked_theme_with_business_needs_and_value_stream() -> None:
    record = extract_idmt_record(ticket_id="IDMT-2099", client=_full_client())
    assert len(record.themes) == 1
    theme = record.themes[0]
    assert theme.group_id == "GROUP-1"
    assert theme.theme_summary == "Prior Auth Theme"
    assert theme.business_needs == "Reduce manual auth review."
    assert theme.value_stream_name == "Manage Utilization Management Program"


def test_extracts_epics_under_themes() -> None:
    record = extract_idmt_record(ticket_id="IDMT-2099", client=_full_client())
    epics = record.themes[0].epics
    assert [e.epic_id for e in epics] == ["EPIC-10", "EPIC-11"]
    assert epics[0].status == "Open"
    assert epics[0].source == "jira"


def test_missing_attachments_handled_gracefully() -> None:
    client = FakeJiraClient(
        issue={"fields": {"summary": "T", "description": "D"}},
        linked=[],
        attachments=[],
    )
    record = extract_idmt_record(ticket_id="IDMT-1", client=client)
    assert record.attachment_text == ""
    assert record.extracted_text == ""  # no idea card, no attachments
    assert "no attachments found" in record.source_metadata["warnings"]


def test_missing_linked_themes_handled_gracefully() -> None:
    client = FakeJiraClient(issue={"fields": {"summary": "T"}}, linked=[], attachments=[])
    record = extract_idmt_record(ticket_id="IDMT-1", client=client)
    assert record.themes == []
    assert "no linked themes found" in record.source_metadata["warnings"]


def test_include_flags_skip_sections() -> None:
    record = extract_idmt_record(
        ticket_id="IDMT-2099",
        client=_full_client(),
        include_attachments=False,
        include_themes=False,
    )
    assert record.attachment_text == ""
    assert record.themes == []


def test_missing_client_method_is_lenient() -> None:
    class Bare:
        def get_issue(self, ticket_id):
            return {"fields": {"summary": "Only issue"}}

    record = extract_idmt_record(ticket_id="IDMT-9", client=Bare())
    assert record.title == "Only issue"
    warnings = record.source_metadata["warnings"]
    assert any("get_attachments" in w for w in warnings)
    assert any("get_linked_issues" in w for w in warnings)


def test_to_dict_shape_is_stable() -> None:
    record = extract_idmt_record(ticket_id="IDMT-2099", client=_full_client())
    payload = record.to_dict()
    assert set(payload) == {
        "ticket_id",
        "title",
        "summary",
        "description",
        "idea_card_text",
        "attachment_text",
        "extracted_text",
        "themes",
        "source_metadata",
        "raw",
    }
    assert set(payload["themes"][0]) == {
        "group_id",
        "theme_summary",
        "business_needs",
        "value_stream_id",
        "value_stream_name",
        "epics",
        "raw",
    }
    assert set(payload["themes"][0]["epics"][0]) == {
        "epic_id",
        "summary",
        "status",
        "source",
        "raw",
    }


def test_attachment_helpers_are_pure() -> None:
    assert combine_attachment_texts(["a", "", "  ", "b"]) == "a\n\nb"
    assert normalize_attachment_metadata({"id": "x", "name": "f.txt", "mime_type": "text/plain"}) == {
        "id": "x",
        "filename": "f.txt",
        "mime_type": "text/plain",
        "size": None,
        "url": "",
    }
    assert normalize_attachment_metadata("not a dict") == {}
