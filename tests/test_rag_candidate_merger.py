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
    assert semantic_row["candidate_status_reason"] == "protected_semantic_lane"
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


def test_merge_candidate_sources_prioritizes_repeated_historical_gap_within_lane() -> None:
    semantic_candidates = [
        {
            "entity_id": f"sem-{idx}",
            "entity_name": f"Semantic Candidate {idx}",
            "description": "Direct semantic fit",
            "semantic_score": 1.8 - (idx * 0.03),
        }
        for idx in range(12)
    ]
    historical_support = [
        {
            "entity_id": "hist-noisy-direct",
            "entity_name": "Noisy Direct Historical Candidate",
            "support_count": 2,
            "direct_count": 2,
            "implied_count": 0,
            "weighted_support_count": 1.8,
            "weighted_direct_count": 1.8,
            "weighted_implied_count": 0.0,
            "best_support_score": 0.92,
            "avg_support_score": 0.58,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2"],
            "historical_reasons": ["[IDMT-1 / direct] narrow analog"],
            "label_sources": ["jira_issuelinks"],
        },
        {
            "entity_id": "hist-repeated-gap",
            "entity_name": "Manage Invoice and Payment Receipt",
            "support_count": 10,
            "direct_count": 0,
            "implied_count": 10,
            "weighted_support_count": 0.88,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 0.88,
            "best_support_score": 0.867,
            "avg_support_score": 0.62,
            "supporting_ticket_ids": [
                "IDMT-10",
                "IDMT-11",
                "IDMT-12",
                "IDMT-13",
                "IDMT-14",
                "IDMT-15",
            ],
            "historical_reasons": ["[IDMT-10 / implied] recurring billing analog"],
            "label_sources": ["jira_themes_fallback"],
        },
        {
            "entity_id": "hist-repeated-util",
            "entity_name": "Manage Utilization Management Program",
            "support_count": 6,
            "direct_count": 0,
            "implied_count": 6,
            "weighted_support_count": 0.86,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 0.86,
            "best_support_score": 0.867,
            "avg_support_score": 0.62,
            "supporting_ticket_ids": ["IDMT-20", "IDMT-21", "IDMT-22", "IDMT-23"],
            "historical_reasons": ["[IDMT-20 / implied] recurring utilization analog"],
            "label_sources": ["jira_themes_fallback"],
        },
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support, max_llm_candidates=5)
    llm_names = [row["entity_name"] for row in result["llm_candidates"]]

    assert "Manage Invoice and Payment Receipt" in llm_names
    assert "Manage Utilization Management Program" in llm_names
    assert len([name for name in llm_names if name.startswith("Semantic Candidate")]) <= 2


def test_merge_candidate_sources_auto_selects_strong_confirmed_candidate_with_four_hits() -> None:
    semantic_candidates = [
        {
            "entity_id": "vs-appeal",
            "entity_name": "Appeal Decision",
            "description": "Review and reconsider claim or coverage decisions.",
            "semantic_score": 1.562,
        }
    ]
    historical_support = [
        {
            "entity_id": "vs-appeal",
            "entity_name": "Appeal Decision",
            "support_count": 4,
            "direct_count": 2,
            "implied_count": 2,
            "weighted_support_count": 1.6,
            "weighted_direct_count": 1.0,
            "weighted_implied_count": 0.6,
            "best_support_score": 0.721,
            "avg_support_score": 0.62,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2", "IDMT-3", "IDMT-4"],
            "supporting_chunk_ids": ["chunk-1"],
            "historical_reasons": ["[IDMT-1 / direct] claim appeal workflow"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support)

    row = result["merged_candidates"][0]
    assert row["entity_name"] == "Appeal Decision"
    assert row["candidate_status"] == "auto_selected"
    assert row["candidate_status_reason"] == "cross_confirmed_semantic_and_historical"
    assert [selected["entity_name"] for selected in result["auto_selected_value_streams"]] == [
        "Appeal Decision"
    ]
    reason = result["auto_selected_value_streams"][0]["reason"]
    assert "claim appeal workflow" in reason
    assert not _has_score_language(reason)
    assert result["llm_candidates"] == []


def test_merge_candidate_sources_keeps_weaker_confirmed_candidate_in_llm_review() -> None:
    semantic_candidates = [
        {
            "entity_id": "vs-borderline",
            "entity_name": "Borderline Confirmed Candidate",
            "description": "Related but less historically confirmed workflow.",
            "semantic_score": 1.562,
        }
    ]
    historical_support = [
        {
            "entity_id": "vs-borderline",
            "entity_name": "Borderline Confirmed Candidate",
            "support_count": 4,
            "direct_count": 2,
            "implied_count": 2,
            "weighted_support_count": 1.6,
            "weighted_direct_count": 1.0,
            "weighted_implied_count": 0.6,
            "best_support_score": 0.69,
            "avg_support_score": 0.62,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2", "IDMT-3", "IDMT-4"],
            "historical_reasons": ["[IDMT-1 / direct] related workflow"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources(semantic_candidates, historical_support)

    row = result["merged_candidates"][0]
    assert row["candidate_status"] == "sent_to_llm"
    assert row["candidate_status_reason"] == "protected_confirmed_lane"
    assert result["auto_selected_value_streams"] == []
    assert result["llm_candidates"][0]["entity_name"] == "Borderline Confirmed Candidate"


def test_merge_candidate_sources_sends_four_hit_historical_only_to_llm_review() -> None:
    historical_support = [
        {
            "entity_id": "hist-receive-care",
            "entity_name": "Receive Care",
            "support_count": 4,
            "direct_count": 4,
            "implied_count": 0,
            "weighted_support_count": 1.8,
            "weighted_direct_count": 1.8,
            "weighted_implied_count": 0.0,
            "best_support_score": 0.691,
            "avg_support_score": 0.62,
            "supporting_ticket_ids": ["IDMT-8255"],
            "historical_reasons": ["[IDMT-8255 / direct] broad behavioral health hub analog"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources([], historical_support, max_llm_candidates=4)

    row = result["merged_candidates"][0]
    assert row["candidate_lane"] == "historical_recall"
    assert row["candidate_status"] == "sent_to_llm"
    assert row["candidate_status_reason"] == "protected_historical_lane"
    assert result["auto_selected_value_streams"] == []


def test_merge_candidate_sources_sends_eight_hit_moderate_historical_only_to_llm_review() -> None:
    historical_support = [
        {
            "entity_id": "hist-adjudicate",
            "entity_name": "Adjudicate Claim",
            "support_count": 8,
            "direct_count": 4,
            "implied_count": 4,
            "weighted_support_count": 2.2,
            "weighted_direct_count": 1.4,
            "weighted_implied_count": 0.8,
            "best_support_score": 0.691,
            "avg_support_score": 0.61,
            "supporting_ticket_ids": ["IDMT-8255", "IDMT-8256"],
            "historical_reasons": ["[IDMT-8255 / implied] broad behavioral health hub analog"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources([], historical_support, max_llm_candidates=4)

    row = result["merged_candidates"][0]
    assert row["candidate_lane"] == "historical_recall"
    assert row["candidate_status"] == "sent_to_llm"
    assert row["candidate_status_reason"] == "protected_historical_lane"
    assert result["auto_selected_value_streams"] == []


def test_merge_candidate_sources_still_auto_selects_extreme_historical_only_consensus() -> None:
    historical_support = [
        {
            "entity_id": "hist-extreme",
            "entity_name": "Extreme Historical Candidate",
            "support_count": 6,
            "direct_count": 4,
            "implied_count": 2,
            "weighted_support_count": 2.1,
            "weighted_direct_count": 1.7,
            "weighted_implied_count": 0.4,
            "best_support_score": 0.82,
            "avg_support_score": 0.68,
            "supporting_ticket_ids": ["IDMT-1", "IDMT-2", "IDMT-3", "IDMT-4"],
            "historical_reasons": ["[IDMT-1 / direct] repeated matching workflow"],
            "label_sources": ["jira_issuelinks"],
        }
    ]

    result = merge_candidate_sources([], historical_support, max_llm_candidates=4)

    row = result["merged_candidates"][0]
    assert row["candidate_lane"] == "historical_recall"
    assert row["candidate_status"] == "auto_selected"
    assert row["candidate_status_reason"] == "strong_historical_support"
    assert [selected["entity_name"] for selected in result["auto_selected_value_streams"]] == [
        "Extreme Historical Candidate"
    ]
    reason = result["auto_selected_value_streams"][0]["reason"]
    assert "repeated matching workflow" in reason
    assert not _has_score_language(reason)


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


def test_merge_candidate_sources_admits_moderate_repeated_support_after_source_exclusion() -> None:
    historical_support = [
        {
            "entity_id": "hist-invoice",
            "entity_name": "Manage Invoice and Payment Receipt",
            "support_count": 10,
            "direct_count": 0,
            "implied_count": 10,
            "weighted_support_count": 0.68,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 0.68,
            "best_support_score": 0.641,
            "avg_support_score": 0.56,
            "supporting_ticket_ids": [
                "IDMT-8199",
                "IDMT-12167",
                "IDMT-31170",
                "IDMT-31171",
            ],
            "historical_reasons": ["[IDMT-8199 / implied] recurring payment workflow"],
            "label_sources": ["jira_themes_fallback"],
        }
    ]

    result = merge_candidate_sources([], historical_support, max_llm_candidates=4)

    row = result["merged_candidates"][0]
    assert row["candidate_status"] == "sent_to_llm"
    assert row["candidate_status_reason"] == "protected_historical_lane"
    assert result["llm_candidates"][0]["entity_name"] == "Manage Invoice and Payment Receipt"


def _has_score_language(reason: str) -> bool:
    lower = reason.lower()
    return any(
        marker in lower
        for marker in (
            "score",
            "similarity",
            "support count",
            "historical tickets",
            "weighted support",
            "rank",
        )
    )
