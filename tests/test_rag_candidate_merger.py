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


def test_merge_candidate_sources_exposes_ranking_and_candidate_status() -> None:
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
            "entity_id": "vs-2",
            "entity_name": "Manage Member Care",
            "support_count": 2,
            "direct_count": 1,
            "implied_count": 1,
            "weighted_support_count": 1.1,
            "weighted_direct_count": 0.7,
            "weighted_implied_count": 0.4,
            "best_support_score": 0.73,
            "avg_support_score": 0.68,
            "supporting_ticket_ids": ["IDMT-10", "IDMT-11"],
            "historical_reasons": ["[IDMT-10 / direct] member support workflow"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support, max_llm_candidates=5)

    by_name = {row["entity_name"]: row for row in result["merged_candidates"]}
    semantic_row = by_name["Conduct Audit"]
    historical_row = by_name["Manage Member Care"]

    assert semantic_row["ranking_score"] == semantic_row["semantic_score"]
    assert semantic_row["candidate_status"] == "sent_to_llm"
    assert semantic_row["candidate_status_reason"] == "within_llm_candidate_cap"

    assert historical_row["ranking_score"] > 0
    assert historical_row["historical_strength"] > historical_row["best_support_score"]
    assert historical_row["candidate_status"] == "sent_to_llm"
    assert historical_row["candidate_status_reason"] == "within_llm_candidate_cap"
