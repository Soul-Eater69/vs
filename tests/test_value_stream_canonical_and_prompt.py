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


def test_candidate_prompt_formats_structured_historical_evidence() -> None:
    block = format_review_pool_candidate_blocks(
        [
            {
                "entity_name": "Issue Payment",
                "entity_id": "vs1",
                "lane": "semantic_plus_historical",
                "from_historical": True,
                "supporting_ticket_count": 1,
                "support_count": 1,
                "direct_count": 0,
                "implied_count": 1,
                "best_support_score": 0.91,
                "avg_support_score": 0.91,
                "weighted_support": 0.6,
                "supporting_ticket_ids": ["IDMT-2"],
                "historical_evidence": [
                    {
                        "ticket_id": "IDMT-2",
                        "title": "Payment workflow modernization",
                        "summary_preview": "Prior ticket changed payment operations and reconciliation.",
                        "inference_type": "implied",
                        "reason": "Payment issuance had to change downstream.",
                        "direct_functions_canonical": ["Configure benefit"],
                        "implied_functions_canonical": ["Issue payment", "Reconcile payment"],
                    }
                ],
            }
        ],
        prompt_budget={
            "analogs_per_candidate": 2,
            "analog_chars": 120,
            "historical_ticket_ids_per_candidate": 2,
        },
    )

    assert "Evidence:" in block
    assert "- IDMT-2 / implied: Payment workflow modernization" in block
    assert "Why this stream: Payment issuance had to change downstream." in block
    assert "Prior ticket summary: Prior ticket changed payment operations and reconciliation." in block
    assert "Supporting functions: direct functions: Configure benefit; implied functions: Issue payment, Reconcile payment" in block
