from __future__ import annotations

from vs_app.modules.rag.query.views import extract_source_ticket_title
from vs_app.modules.themes.title_builder import (
    build_theme_identity_key,
    build_theme_payloads,
    build_value_stream_theme_title,
)


def test_build_value_stream_theme_title_format() -> None:
    title = build_value_stream_theme_title(
        "CP 2025 Health Management & Advocacy: Digital GTM",
        "Establish Product Offering",
    )

    assert title == (
        "CP 2025 Health Management & Advocacy: Digital GTM - "
        "Establish Product Offering"
    )


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
