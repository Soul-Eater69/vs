from vs_app.modules.rag.augmentation.foundational_signals import (
    annotate_foundational_signals,
    foundational_signal_names,
)
from vs_app.modules.rag.augmentation.prompt_context import format_review_pool_candidate_blocks


def test_order_to_cash_alias_signal_annotates_canonical_candidate() -> None:
    candidates = [
        {
            "entity_id": "VS-1",
            "entity_name": "Order to Cash for Group Coverage",
            "lane": "semantic_plus_historical",
        }
    ]

    annotated = annotate_foundational_signals(candidates, "Foundational stream: Order to Cash")

    assert foundational_signal_names("Foundational stream: Order to Cash") == ["Order to Cash"]
    assert annotated[0]["foundational_signal"] is True
    assert annotated[0]["foundational_match_text"] == "Order to Cash"
    assert annotated[0]["foundational_match_type"] == "alias"


def test_exact_foundational_signal_is_printed_in_review_pool_candidate_block() -> None:
    candidates = annotate_foundational_signals(
        [
            {
                "entity_id": "VS-2",
                "entity_name": "Establish Product Offering",
                "lane": "semantic_only",
            }
        ],
        "Current card lists Establish Product Offering.",
    )

    block = format_review_pool_candidate_blocks(candidates)

    assert 'Foundational signal: exact match to "Establish Product Offering"' in block
