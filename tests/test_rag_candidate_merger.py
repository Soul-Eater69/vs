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
    assert semantic_row["candidate_lane"] == "semantic_direct"

    assert historical_row["ranking_score"] > 0
    assert historical_row["historical_strength"] > historical_row["best_support_score"]
    assert historical_row["candidate_status"] == "sent_to_llm"
    assert historical_row["candidate_status_reason"] == "protected_historical_lane"
    assert historical_row["candidate_lane"] == "historical_recall"


def test_merge_candidate_sources_protects_historical_recall_from_semantic_crowding() -> None:
    semantic_candidates = [
        {
            "entity_id": f"sem-{idx}",
            "entity_name": f"Semantic Candidate {idx}",
            "description": "Direct semantic fit",
            "semantic_score": 1.5 - (idx * 0.05),
        }
        for idx in range(6)
    ]
    historical_support = [
        {
            "entity_id": "hist-1",
            "entity_name": "Historical Recovery Candidate",
            "support_count": 6,
            "direct_count": 0,
            "implied_count": 6,
            "weighted_support_count": 1.2,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 1.2,
            "best_support_score": 0.82,
            "avg_support_score": 0.74,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2", "IDMT-3"],
            "historical_reasons": ["[IDMT-1 / implied] similar digital care rollout"],
            "label_sources": ["jira_themes_fallback"],
        }
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support, max_llm_candidates=3)

    by_name = {row["entity_name"]: row for row in result["merged_candidates"]}
    historical_row = by_name["Historical Recovery Candidate"]

    assert len(result["llm_candidates"]) == 3
    assert historical_row["candidate_status"] == "sent_to_llm"
    assert historical_row["candidate_status_reason"] == "protected_historical_lane"
    assert any(
        row["entity_name"] == "Historical Recovery Candidate"
        for row in result["llm_candidates"]
    )


def test_merge_candidate_sources_admits_repeated_implied_recovery_when_weighted_support_is_diluted() -> None:
    historical_support = [
        {
            "entity_id": "hist-2",
            "entity_name": "Manage Invoice and Payment Receipt",
            "support_count": 10,
            "direct_count": 0,
            "implied_count": 10,
            "weighted_support_count": 0.82,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 0.82,
            "best_support_score": 0.845,
            "avg_support_score": 0.67,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2", "IDMT-3", "IDMT-4"],
            "historical_reasons": ["[IDMT-1 / implied] recurring payment and billing workflow"],
            "label_sources": ["jira_themes_fallback"],
        }
    ]

    result = merge_candidate_sources([], historical_support, max_llm_candidates=4)

    row = result["merged_candidates"][0]
    assert row["candidate_lane"] == "historical_recall"
    assert row["candidate_status"] == "sent_to_llm"
    assert row["candidate_status_reason"] == "protected_historical_lane"
