from __future__ import annotations

from vs_app.modules.rag.query.views import extract_source_ticket_title
from vs_app.modules.themes.title_builder import (
    build_stage_child_issue_title,
    build_theme_identity_key,
    build_theme_payloads,
    build_value_stream_theme_title,
    enrich_stage_predictions_with_titles,
    resolve_theme_title_prefix,
)


def test_parenthetical_product_wins_for_theme_title_prefix() -> None:
    prefix, source = resolve_theme_title_prefix(
        "CP 2025 National Network Product Tiering and Steerage (Coupe Health)",
        "IDMT-1145",
    )
    title = build_value_stream_theme_title(
        prefix,
        "Establish Product Offering",
    )

    assert source == "parenthetical_product"
    assert title == "Coupe Health - Establish Product Offering"


def test_bracket_code_removed_for_theme_title_prefix() -> None:
    prefix, source = resolve_theme_title_prefix(
        "CP 2025 Women's and Family Health [00014020]",
        "IDMT-1145",
    )
    title = build_value_stream_theme_title(prefix, "Configure Price and Quote")

    assert source == "clean_source_ticket_title"
    assert title == "CP 2025 Women's and Family Health - Configure, Price, and Quote"


def test_plain_source_title_fallback() -> None:
    prefix, source = resolve_theme_title_prefix(
        "CP 2024.5 Behavioral Health Hub",
        "IDMT-1145",
    )
    title = build_value_stream_theme_title(prefix, "Manage Member Care")

    assert source == "clean_source_ticket_title"
    assert title == "CP 2024.5 Behavioral Health Hub - Manage Member Care"


def test_ticket_id_last_fallback() -> None:
    prefix, source = resolve_theme_title_prefix(None, "IDMT-1145")
    title = build_value_stream_theme_title(prefix, "Order to Cash")

    assert source == "ticket_id_fallback"
    assert title == "IDMT-1145 - Order to Cash for Group Coverage"


def test_condensed_theme_title_prefix_wins() -> None:
    prefix, source = resolve_theme_title_prefix(
        "CP 2025 Long Parent Title",
        "IDMT-1145",
        theme_title_prefix="Concise Product",
        theme_title_prefix_source="product_or_program_name",
    )

    assert prefix == "Concise Product"
    assert source == "product_or_program_name"


def test_extract_source_ticket_title_from_title_header() -> None:
    raw = "Title: CP 2025 Health Management & Advocacy: Digital GTM\nBody text"

    assert extract_source_ticket_title(raw) == "CP 2025 Health Management & Advocacy: Digital GTM"


def test_identity_key_prefers_entity_id() -> None:
    key = build_theme_identity_key(
        "idmt-123",
        value_stream_entity_id="VSR00074590",
        value_stream_name="Establish Product Offering",
    )

    assert key == "IDMT-123::vs_id::vsr00074590"


def test_identity_key_falls_back_to_normalized_value_stream_name() -> None:
    key = build_theme_identity_key(
        "IDMT-123",
        value_stream_name="Configure, Price, and Quote",
    )

    assert key == "IDMT-123::vs_name::configure price and quote"


def test_theme_payloads_dedupe_by_source_and_entity_id() -> None:
    payloads = build_theme_payloads(
        "IDMT-123",
        "CP 2025 Health Management & Advocacy: Digital GTM",
        [
            {
                "entity_id": "VSR00074590",
                "entity_name": "Establish Product Offering",
                "confidence": 0.82,
                "selection_source": "llm_pick",
            },
            {
                "entity_id": "VSR00074590",
                "entity_name": "Establish Product Offering",
                "confidence": 0.75,
                "selection_source": "safe_backfill",
            },
        ],
    )

    assert len(payloads) == 1
    assert payloads[0]["identity_key"] == "IDMT-123::vs_id::vsr00074590"
    assert payloads[0]["confidence"] == 0.82


def test_theme_title_truncation_preserves_value_stream_suffix() -> None:
    title = build_value_stream_theme_title(
        "A very long source ticket title that should be shortened",
        "Manage Leads and Opportunities",
        max_length=55,
    )

    assert len(title) <= 55
    assert title.endswith(" - Manage Leads and Opportunities")
    assert "..." in title


def test_stage_child_issue_title_format() -> None:
    title = build_stage_child_issue_title(
        "CP 2026 Women's and Family Health",
        "Manage Leads and Opportunities",
        "Perform Outreach to Leads and Prospects",
    )

    assert title == (
        "CP 2026 Women's and Family Health : Manage Leads and Opportunities - "
        "Perform Outreach to Leads and Prospects"
    )


def test_stage_child_issue_title_truncation_preserves_stage_suffix() -> None:
    title = build_stage_child_issue_title(
        "A very long source title that needs to be shortened for Jira child issue limits",
        "Manage Leads and Opportunities",
        "Perform Outreach to Leads and Prospects",
        max_length=82,
    )

    assert len(title) <= 82
    assert title.endswith(" - Perform Outreach to Leads and Prospects")
    assert "..." in title


def test_enrich_stage_predictions_with_titles() -> None:
    enriched = enrich_stage_predictions_with_titles(
        stage_predictions=[
            {
                "value_stream_id": "VSR00074599",
                "value_stream_name": "Manage Leads and Opportunities",
                "stage_scope": "specific_stages",
                "selected_stages": [
                    {
                        "stage_id": "VSS1",
                        "stage_sequence": 4,
                        "stage_name": "Perform Outreach to Leads and Prospects",
                    }
                ],
            }
        ],
        theme_payloads=[
            {
                "value_stream_entity_id": "VSR00074599",
                "value_stream_name": "Manage Leads and Opportunities",
                "theme_prefix": "CP 2026 Women's and Family Health",
            }
        ],
    )

    stage = enriched[0]["selected_stages"][0]
    assert stage["stage_child_issue_title"] == (
        "CP 2026 Women's and Family Health : Manage Leads and Opportunities - "
        "Perform Outreach to Leads and Prospects"
    )
