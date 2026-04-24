from __future__ import annotations

from vs_app.modules.rag.augmentation.candidate_merger import merge_candidate_sources


def test_merge_candidate_sources_preserves_expected_top_level_keys() -> None:
    semantic_candidates = [
        {
            "entity_id": "vs-1",
            "entity_name": "Conduct Audit",
            "description": "Audit workflow support",
            "semantic_score": 1.72,
        }
    ]
    historical_support = [
        {
            "entity_id": "vs-1",
            "entity_name": "Conduct Audit",
            "support_count": 5,
            "direct_count": 4,
            "implied_count": 1,
            "weighted_support_count": 2.4,
            "weighted_direct_count": 2.0,
            "weighted_implied_count": 0.4,
            "best_support_score": 0.83,
            "avg_support_score": 0.71,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2"],
            "supporting_chunk_ids": ["chunk-1"],
            "historical_reasons": ["[IDMT-1 / direct] audit workflow"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support)

    assert set(result) >= {
        "merged_candidates",
        "auto_selected_value_streams",
        "llm_candidates",
    }
