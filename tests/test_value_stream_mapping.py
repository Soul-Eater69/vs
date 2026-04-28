from __future__ import annotations

from vs_app.modules.ingestion.value_stream_labels import value_stream_mapping
from vs_app.modules.ingestion.value_stream_labels.value_stream_mapping import (
    resolve_value_stream_mapping,
)


def test_theme_fallback_resolves_value_stream_prefix_before_product_suffix() -> None:
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

    assert result["label_source"] == "jira_themes_fallback"
    assert result["vs_names"] == [
        "Order to Cash for Group Coverage",
        "Perform Engagement",
        "Resolve Request-Inquiry",
    ]
    assert "Fill and Manage Prescriptions" not in result["vs_names"]


def test_unresolved_theme_fallback_sends_full_raw_line_to_llm(monkeypatch) -> None:
    captured_entries = []

    def fake_verify(entries, llm_client=None):
        captured_entries.extend(entries)
        return {}

    monkeypatch.setattr(value_stream_mapping, "_verify_names_with_llm", fake_verify)

    resolve_value_stream_mapping(
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

    assert captured_entries == [
        {
            "raw_name": "Unknown Prefix : Widget Program",
            "cleaned_name": "Widget Program",
        }
    ]
