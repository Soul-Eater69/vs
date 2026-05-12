from __future__ import annotations

from pydantic import ValidationError

from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.modules.rag.augmentation.prompt_context import format_review_pool_candidate_blocks
from vs_app.modules.value_streams.canonical import (
    canonicalize_value_stream_name,
    expand_domain_signal,
)


def test_order_to_cash_alias() -> None:
    assert canonicalize_value_stream_name("Order to Cash") == "Order to Cash for Group Coverage"


def test_resolve_request_inquiry_alias() -> None:
    assert canonicalize_value_stream_name("Resolve Request Inquiry") == "Resolve Request-Inquiry"


def test_payment_integrity_expansion() -> None:
    expanded = expand_domain_signal("Ensure Payment Integrity")

    assert "Issue Payment" in expanded
    assert "Manage Invoice and Payment Receipt" in expanded


def test_request_rejects_removed_label_injection_fields() -> None:
    removed_field = "found" + "ational_" + "value_streams_canonical"
    try:
        ValueStreamRagRequest(
            idea_card_text="test",
            **{removed_field: ["Order to Cash for Group Coverage"]},
        )
        raise AssertionError("Expected validation error")
    except ValidationError:
        pass


def test_candidate_prompt_has_no_anchor_signal_text() -> None:
    removed_prefix = "found" + "ational_"
    block = format_review_pool_candidate_blocks(
        [
            {
                "entity_name": "Order to Cash for Group Coverage",
                "entity_id": "vs1",
                "lane": "semantic_plus_historical",
                removed_prefix + "signal": True,
                removed_prefix + "match_text": "Order to Cash",
                removed_prefix + "match_type": "alias",
            }
        ]
    )

    assert "Found" + "ational signal" not in block
    assert "Trusted " + "anchor " + "signal" not in block
