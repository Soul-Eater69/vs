from vs_app.modules.rag.augmentation.foundational_signals import (
    annotate_foundational_signals,
    foundational_signal_names,
)
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


def test_annotation_uses_canonical_metadata_without_text() -> None:
    candidates = [
        {
            "entity_id": "VS-1",
            "entity_name": "Order to Cash for Group Coverage",
            "lane": "semantic_plus_historical",
        }
    ]

    annotated = annotate_foundational_signals(
        candidates,
        foundational_value_streams_canonical=["Order to Cash for Group Coverage"],
    )

    assert foundational_signal_names(
        foundational_value_streams_canonical=["Order to Cash for Group Coverage"]
    ) == ["Order to Cash for Group Coverage"]
    assert annotated[0]["foundational_signal"] is True
    assert annotated[0]["foundational_match_type"] == "canonical"


def test_annotation_uses_entity_id() -> None:
    candidates = [
        {"entity_id": "VS_ORDER_TO_CASH", "entity_name": "Some Name"},
    ]

    annotated = annotate_foundational_signals(
        candidates,
        foundational_value_stream_entity_ids=["VS_ORDER_TO_CASH"],
    )

    assert annotated[0]["foundational_signal"] is True
    assert annotated[0]["foundational_match_type"] == "entity_id"


def test_order_to_cash_does_not_need_raw_text() -> None:
    candidates = [
        {"entity_id": "VS-1", "entity_name": "Order to Cash for Group Coverage"},
    ]

    annotated = annotate_foundational_signals(
        candidates,
        foundational_value_streams_canonical=["Order to Cash for Group Coverage"],
        idea_text_fallback="No raw alias here",
    )

    assert annotated[0]["foundational_signal"] is True


def test_exact_foundational_signal_is_printed_in_review_pool_candidate_block() -> None:
    candidates = annotate_foundational_signals(
        [
            {
                "entity_id": "VS-2",
                "entity_name": "Establish Product Offering",
                "lane": "semantic_only",
            }
        ],
        foundational_value_streams_canonical=["Establish Product Offering"],
    )

    block = format_review_pool_candidate_blocks(candidates)

    assert 'Foundational signal: canonical match to "Establish Product Offering"' in block
