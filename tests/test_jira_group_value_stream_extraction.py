from __future__ import annotations

from vs_app.modules.ingestion.jira.mapper import build_ticket_payload
from vs_app.modules.ingestion.jira.value_stream_labels.helpers import clean_value_stream_name
from vs_app.modules.ingestion.jira.value_stream_labels.theme_extraction import resolve_value_streams


def _group_link(key: str, summary: str, status: str = "To Do") -> dict:
    return {
        "type": {"name": "implements", "outward": "implements", "inward": "is implemented by"},
        "outwardIssue": {
            "key": key,
            "fields": {
                "summary": summary,
                "status": {"name": status},
                "issuetype": {"name": "Group"},
            },
        },
    }


def test_clean_value_stream_name_handles_cp_theme_shapes() -> None:
    assert (
        clean_value_stream_name(
            "CP 2025 Health Management & Advocacy: Digital GTM - Establish Product Offering"
        )
        == "Establish Product Offering"
    )
    assert (
        clean_value_stream_name(
            "CP 2025 Health Management & Advocacy: HAS Lite - Manage Invoice and Payment Receipt"
        )
        == "Manage Invoice and Payment Receipt"
    )
    assert (
        clean_value_stream_name("CP 2026 Women's and Family Health : Manage Member Care")
        == "Manage Member Care"
    )


def test_implemented_by_group_link_populates_value_stream_names() -> None:
    issue = {
        "key": "IDMT-1",
        "fields": {
            "attachment": [],
            "issuelinks": [
                _group_link(
                    "GROUP-19626",
                    "CP 2025 Health Management & Advocacy: Digital GTM - Establish Product Offering",
                    "In Progress",
                )
            ],
        },
    }

    payload = build_ticket_payload(issue, ticket_id="IDMT-1")

    assert payload["value_stream_names"] == ["Establish Product Offering"]
    assert payload["value_stream_ids"] == ["GROUP-19626"]
    assert payload["value_stream_statuses"] == ["In Progress"]
    assert payload["value_stream_label_source"] == "jira_implemented_by_group_links"


def test_multiple_group_links_dedupe_without_alignment_mismatch() -> None:
    issue_links = [
        _group_link("GROUP-1", "CP 2025 Alpha: Build Thing", "To Do"),
        _group_link("GROUP-2", "CP 2025 Alpha: Build Thing", "Done"),
        _group_link("GROUP-3", "CP 2025 Alpha: Run Thing", "In Progress"),
    ]
    themes = [
        {
            "key": link["outwardIssue"]["key"],
            "summary": clean_value_stream_name(link["outwardIssue"]["fields"]["summary"]),
            "summary_raw": link["outwardIssue"]["fields"]["summary"],
            "status": link["outwardIssue"]["fields"]["status"]["name"],
        }
        for link in issue_links
    ]

    result = resolve_value_streams(themes, issue_links)

    assert result["value_stream_names"] == ["Build Thing", "Run Thing"]
    assert result["value_stream_ids"] == ["GROUP-1", "GROUP-3"]
    assert result["value_stream_statuses"] == ["To Do", "In Progress"]
    assert len(result["linked_value_streams"]) == 2


def test_explicit_value_stream_link_fallback_still_works() -> None:
    issue_links = [
        {
            "type": {"name": "Value Stream"},
            "outwardIssue": {
                "key": "VS-1",
                "fields": {
                    "summary": "CP 2025 Portfolio: Manage Member Care",
                    "status": {"name": "Done"},
                },
            },
        }
    ]

    result = resolve_value_streams([], issue_links)

    assert result["value_stream_names"] == ["Manage Member Care"]
    assert result["value_stream_ids"] == ["VS-1"]
    assert result["value_stream_label_source"] == "jira_issuelinks"
