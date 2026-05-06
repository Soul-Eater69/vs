from __future__ import annotations

from vs_app.ingestion.jira.value_stream_labels.value_stream_mapping import (
    canonicalize_value_stream_names,
    resolve_theme_value_stream_mapping,
    resolve_value_stream_mapping,
)


def test_missing_value_stream_links_returns_no_labels() -> None:
    result = resolve_value_stream_mapping(
        {
            "themes": [
                {
                    "key": "GROUP-21875",
                    "summary": "ERX ASO Amazon Mail Order Pharmacy",
                    "summary_raw": "Order to Cash : ERX ASO Amazon Mail Order Pharmacy",
                    "status": "TO DO",
                    "issue_type": "Theme",
                },
                {
                    "key": "GROUP-21877",
                    "summary": "ERX ASO Amazon Mail Order Pharmacy",
                    "summary_raw": "Perform Engagement: ERX ASO Amazon Mail Order Pharmacy",
                    "status": "TO DO",
                    "issue_type": "Theme",
                },
                {
                    "key": "GROUP-21876",
                    "summary": "Resolve Request Inquiry- ERX ASO Amazon Mail Order Pharmacy",
                    "summary_raw": "Resolve Request Inquiry- ERX ASO Amazon Mail Order Pharmacy",
                    "status": "TO DO",
                    "issue_type": "Theme",
                },
            ]
        },
        {"vs": []},
    )

    assert result["label_source"] == "jira_issuelinks"
    assert result["vs_names"] == []
    assert result["vs_ids"] == []
    assert result["linked_value_streams"] == []


def test_missing_value_stream_links_does_not_call_llm_verifier(monkeypatch) -> None:
    from vs_app.ingestion.jira.value_stream_labels import value_stream_mapping

    def fake_verify(entries, llm_client=None):
        raise AssertionError("LLM verifier should not run without VS issue links")

    monkeypatch.setattr(value_stream_mapping, "_verify_names_with_llm", fake_verify)

    result = resolve_value_stream_mapping(
        {
            "themes": [
                {
                    "key": "GROUP-1",
                    "summary": "Widget Program",
                    "summary_raw": "Unknown Prefix : Widget Program",
                    "status": "TO DO",
                    "issue_type": "Theme",
                }
            ]
        },
        {"vs": []},
        llm_client=object(),
    )

    assert result["vs_names"] == []


def test_theme_summary_that_resolves_to_registry_is_kept() -> None:
    result = resolve_theme_value_stream_mapping(
        [
            {
                "key": "GROUP-1",
                "summary": "Issue Payment",
                "summary_raw": "CP 2025 Alpha: Issue Payment",
                "status": "Active",
            }
        ]
    )

    assert result["value_stream_names"] == ["Issue Payment"]
    assert result["value_stream_ids"] == ["VSR00074596"]
    assert result["jira_group_ids"] == ["GROUP-1"]
    assert result["value_stream_statuses"] == ["Active"]


def test_theme_summary_that_verifier_maps_is_kept(monkeypatch) -> None:
    from vs_app.ingestion.jira.value_stream_labels import value_stream_mapping

    def fake_verify(entries, llm_client=None):
        assert entries == [
            {
                "key": "GROUP-1",
                "raw_name": "CP 2025 Alpha: Build Thing",
                "cleaned_name": "Build Thing",
                "status": "Active",
            }
        ]
        return {"cp 2025 alpha: build thing": "Issue Payment"}

    monkeypatch.setattr(value_stream_mapping, "_verify_names_with_llm", fake_verify)

    result = resolve_theme_value_stream_mapping(
        [
            {
                "key": "GROUP-1",
                "summary": "Build Thing",
                "summary_raw": "CP 2025 Alpha: Build Thing",
                "status": "Active",
            }
        ],
        llm_client=object(),
    )

    assert result["value_stream_names"] == ["Issue Payment"]
    assert result["value_stream_ids"] == ["VSR00074596"]
    assert result["jira_group_ids"] == ["GROUP-1"]
    assert result["value_stream_statuses"] == ["Active"]


def test_canonicalize_value_stream_names_drops_unresolved(monkeypatch) -> None:
    from vs_app.ingestion.jira.value_stream_labels import value_stream_mapping

    monkeypatch.setattr(value_stream_mapping, "_verify_names_with_llm", lambda entries, llm_client=None: {})

    assert canonicalize_value_stream_names(["Build Thing"], llm_client=object()) == []
