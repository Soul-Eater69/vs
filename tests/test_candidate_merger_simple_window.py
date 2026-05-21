from __future__ import annotations

from vs_app.modules.rag.augmentation.candidate_merger import (
    CandidateWindowPolicy,
    merge_candidate_sources,
)


def test_simple_lanes_and_no_auto_selection() -> None:
    result = merge_candidate_sources(
        [
            {
                "entity_id": "vs-both",
                "entity_name": "Issue Payment",
                "description": "Issue payment workflow",
                "semantic_score": 1.4,
            },
            {
                "entity_id": "vs-sem",
                "entity_name": "Manage Leads",
                "description": "Lead management",
                "semantic_score": 1.2,
            },
        ],
        [
            {
                "entity_id": "vs-both",
                "entity_name": "Issue Payment",
                "supporting_ticket_ids": ["H1", "H2"],
                "support_count": 2,
                "direct_count": 1,
                "implied_count": 1,
                "best_support_score": 0.84,
                "avg_support_score": 0.80,
                "weighted_support_count": 1.6,
            },
            {
                "entity_id": "vs-hist",
                "entity_name": "Receive Care",
                "supporting_ticket_ids": ["H3"],
                "support_count": 1,
                "direct_count": 0,
                "implied_count": 1,
                "best_support_score": 0.81,
                "avg_support_score": 0.81,
                "weighted_support_count": 1.0,
            },
        ],
    )

    by_name = {row["entity_name"]: row for row in result["merged_candidates"]}

    assert by_name["Issue Payment"]["lane"] == "semantic_plus_historical"
    assert by_name["Issue Payment"]["bucket"] == "semantic_plus_historical"
    assert by_name["Issue Payment"]["candidate_lane"] == "semantic_plus_historical"
    assert by_name["Manage Leads"]["lane"] == "semantic_only"
    assert by_name["Receive Care"]["lane"] == "historical_only"
    assert result["auto_selected_value_streams"] == []
    assert all(row["candidate_status"] != "auto_selected" for row in result["merged_candidates"])


def test_outside_window_is_not_called_dropped_before_llm() -> None:
    result = merge_candidate_sources(
        [
            {
                "entity_id": f"sem-{idx}",
                "entity_name": f"Semantic {idx}",
                "semantic_score": 2.0 - idx * 0.1,
            }
            for idx in range(3)
        ],
        [],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=0,
            max_semantic_only=1,
            max_historical_only=0,
        ),
    )

    statuses = {row["entity_name"]: row["candidate_status"] for row in result["merged_candidates"]}
    assert statuses["Semantic 0"] == "sent_to_llm"
    assert statuses["Semantic 1"] == "outside_llm_window"
    assert statuses["Semantic 2"] == "outside_llm_window"
    assert all(status != "dropped_before_llm" for status in statuses.values())


def test_historical_only_sorting_prefers_close_analog_over_larger_weak_count() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": "Manage Member Care",
                "supporting_ticket_ids": ["H1", "H2", "H3", "H4", "H5"],
                "support_count": 5,
                "best_support_score": 0.62,
                "avg_support_score": 0.58,
                "weighted_support_count": 1.2,
            },
            {
                "entity_name": "Issue Payment",
                "supporting_ticket_ids": ["H1", "H2"],
                "support_count": 2,
                "best_support_score": 0.86,
                "avg_support_score": 0.82,
                "weighted_support_count": 1.6,
            },
        ],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=0,
            max_semantic_only=0,
            max_historical_only=2,
        ),
    )

    assert [row["entity_name"] for row in result["llm_candidates"]] == [
        "Issue Payment",
        "Manage Member Care",
    ]


def test_supporting_ticket_count_is_unique_and_support_count_alias_remains() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": "Issue Payment",
                "supporting_ticket_ids": ["H1", "H1", "H2"],
                "support_count": 9,
                "best_support_score": 0.86,
                "avg_support_score": 0.82,
            }
        ],
    )

    row = result["merged_candidates"][0]
    assert row["supporting_ticket_count"] == 2
    assert row["support_count"] == 2
    assert row["weighted_support"] == 2.0


def test_historical_evidence_is_preserved_without_affecting_window() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": "Issue Payment",
                "supporting_ticket_ids": ["H1", "H2", "H3"],
                "support_count": 3,
                "direct_count": 1,
                "implied_count": 2,
                "best_support_score": 0.86,
                "avg_support_score": 0.82,
                "historical_evidence": [
                    {
                        "ticket_id": "H1",
                        "inference_type": "direct",
                        "reason": "Payment was directly tagged.",
                    },
                    {
                        "ticket_id": "H2",
                        "inference_type": "implied",
                        "reason": "Payment changed downstream.",
                    },
                    {
                        "ticket_id": "H3",
                        "inference_type": "implied",
                        "reason": "Extra evidence is capped for prompt size.",
                    },
                ],
            }
        ],
        policy=CandidateWindowPolicy(max_supporting_tickets_per_candidate=2),
    )

    row = result["merged_candidates"][0]
    assert [item["ticket_id"] for item in row["historical_evidence"]] == ["H1", "H2"]
    assert [row["entity_name"] for row in result["llm_candidates"]] == ["Issue Payment"]


def test_low_score_historical_prior_can_reach_llm() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": "Weak Historical Stream",
                "supporting_ticket_ids": ["H1"],
                "support_count": 1,
                "direct_count": 1,
                "best_support_score": 0.03,
                "avg_support_score": 0.03,
                "weighted_support_count": 1.0,
            }
        ],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=0,
            max_semantic_only=0,
            max_historical_only=1,
        ),
    )

    assert [row["entity_name"] for row in result["llm_candidates"]] == [
        "Weak Historical Stream"
    ]
    assert result["merged_candidates"][0]["candidate_status"] == "sent_to_llm"


def test_single_weak_implied_historical_prior_is_gated_out() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": "Implied Historical Stream",
                "supporting_ticket_ids": ["H1"],
                "support_count": 1,
                "direct_count": 0,
                "implied_count": 1,
                "best_support_score": 0.03,
                "avg_support_score": 0.03,
                "weighted_support_count": 0.1,
            }
        ],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=0,
            max_semantic_only=0,
            max_historical_only=1,
        ),
    )

    assert result["llm_candidates"] == []
    assert result["merged_candidates"][0]["candidate_status"] == "outside_llm_window"


def test_semantic_only_does_not_reuse_historical_lane_capacity() -> None:
    result = merge_candidate_sources(
        [
            {
                "entity_id": f"sem-{idx}",
                "entity_name": f"Semantic {idx}",
                "semantic_score": 0.5 + idx * 0.01,
            }
            for idx in range(6)
        ],
        [],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=4,
            max_semantic_only=1,
            max_historical_only=4,
        ),
        max_llm_candidates=6,
    )

    assert len(result["llm_candidates"]) == 0
    assert result["candidate_window_counts"]["semantic_only"] == 0
    assert all(row["lane"] == "semantic_only" for row in result["llm_candidates"])


def test_historical_value_stream_candidates_are_not_capped_by_ticket_hit_top_k() -> None:
    result = merge_candidate_sources(
        [],
        [
            {
                "entity_name": f"Historical Stream {idx}",
                "supporting_ticket_ids": ["IDMT-1"],
                "support_count": 1,
                "direct_count": 1,
                "best_support_score": 0.9,
                "avg_support_score": 0.9,
                "weighted_support_count": 1.0,
            }
            for idx in range(8)
        ],
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=0,
            max_semantic_only=0,
            max_historical_only=10,
        ),
    )

    assert len(result["llm_candidates"]) == 8
