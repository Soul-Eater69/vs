from __future__ import annotations

from vs_app.modules.rag.augmentation.prompt_context import format_candidate_blocks


def test_candidate_blocks_show_simple_lane_and_hide_formula_fields() -> None:
    text = format_candidate_blocks(
        [
            {
                "entity_id": "vs-1",
                "entity_name": "Issue Payment",
                "lane": "semantic_plus_historical",
                "bucket": "semantic_plus_historical",
                "candidate_lane": "semantic_plus_historical",
                "from_semantic": True,
                "semantic_score": 1.42,
                "from_historical": True,
                "supporting_ticket_count": 2,
                "support_count": 2,
                "direct_count": 1,
                "implied_count": 1,
                "best_support_score": 0.84,
                "avg_support_score": 0.81,
                "weighted_support": 1.6,
                "supporting_ticket_ids": ["IDMT-1", "IDMT-2"],
                "ranking_score": 99,
                "historical_strength": 88,
                "historical_reasons": ["[IDMT-1 / direct] payment issuing workflow"],
            }
        ]
    )

    assert "Lane: semantic_plus_historical" in text
    assert "ranking_score" not in text
    assert "historical_strength" not in text
    assert "Historical support: 2 unique tickets (1 direct, 1 implied)" in text
    assert "Best historical similarity: 0.8400" in text
    assert "Average historical similarity: 0.8100" in text
    assert "Weighted historical support: 1.6000" in text
    assert "Supporting tickets: IDMT-1, IDMT-2" in text
