from __future__ import annotations

from vs_app.modules.rag.augmentation.finalizer import (
    _direct_selection_max,
    _finalize_selected,
    _split_llm_candidates,
)


def _historical_candidate(name: str, *, best: float, avg: float, support: int = 3) -> dict:
    return {
        "entity_id": name.lower().replace(" ", "-"),
        "entity_name": name,
        "candidate_lane": "historical_recall",
        "from_historical": True,
        "from_semantic": False,
        "support_count": support,
        "direct_count": 1,
        "implied_count": max(0, support - 1),
        "weighted_support_count": 0.9,
        "best_support_score": best,
        "avg_support_score": avg,
        "supporting_ticket_ids": [f"IDMT-{idx}" for idx in range(1, support + 1)],
        "description": f"{name} business workflow.",
        "historical_reasons": ["[IDMT-1 / direct] recurring downstream workflow analog"],
    }


def _confirmed_candidate(
    name: str,
    *,
    semantic: float,
    best: float,
    support: int,
    weighted: float | None = None,
) -> dict:
    return {
        "entity_id": name.lower().replace(" ", "-"),
        "entity_name": name,
        "candidate_lane": "confirmed_direct",
        "from_historical": True,
        "from_semantic": True,
        "semantic_score": semantic,
        "ranking_score": semantic + 0.25 * best,
        "support_count": support,
        "direct_count": 1,
        "implied_count": max(0, support - 1),
        "weighted_support_count": support if weighted is None else weighted,
        "best_support_score": best,
        "avg_support_score": 0.62,
        "supporting_ticket_ids": [f"IDMT-{idx}" for idx in range(1, support + 1)],
        "description": f"{name} business workflow.",
        "historical_reasons": ["[IDMT-1 / direct] recurring direct workflow analog"],
    }


def _selected(name: str) -> dict:
    return {
        "entity_id": name.lower().replace(" ", "-"),
        "entity_name": name,
        "confidence": 0.72,
        "reason": "LLM selected",
    }


def test_split_llm_candidates_keeps_direct_and_historical_passes_disjoint() -> None:
    direct, historical = _split_llm_candidates(
        [
            {
                "entity_name": "Confirmed",
                "candidate_lane": "confirmed_direct",
                "from_semantic": True,
                "from_historical": True,
            },
            {
                "entity_name": "Historical",
                "candidate_lane": "historical_recall",
                "from_semantic": False,
                "from_historical": True,
            },
            {
                "entity_name": "Semantic",
                "candidate_lane": "semantic_direct",
                "from_semantic": True,
                "from_historical": False,
            },
        ]
    )

    assert [row["entity_name"] for row in direct] == ["Confirmed", "Semantic"]
    assert [row["entity_name"] for row in historical] == ["Historical"]


def test_direct_selection_max_scales_for_many_confirmed_candidates() -> None:
    assert _direct_selection_max(8) == 10
    assert _direct_selection_max(30) == 15
    assert _direct_selection_max(50) == 18


def test_direct_selection_max_uses_evidence_budget_for_noisy_direct_prompt() -> None:
    weak_candidates = [
        _confirmed_candidate(f"Weak Confirmed {idx}", semantic=1.15, best=0.62, support=3)
        for idx in range(30)
    ]
    strong_candidates = [
        _confirmed_candidate(f"Strong Confirmed {idx}", semantic=1.36, best=0.62, support=3)
        for idx in range(4)
    ]

    assert _direct_selection_max([*weak_candidates, *strong_candidates]) == 10


def test_finalize_selected_drops_weak_historical_only_llm_selection() -> None:
    weak_candidate = _historical_candidate("Receive Care", best=0.691, avg=0.62, support=4)
    weak_selection = _selected("Receive Care")
    weak_selection["confidence"] = 0.60

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[weak_selection],
        llm_candidates=[weak_candidate],
    )

    assert final_selected == []
    assert filtered_llm == []
    assert rescued_confirmed == []
    assert rescued == []
    assert dropped[0]["entity_name"] == "Receive Care"
    assert dropped[0]["drop_reason"] == "weak_historical_gap_fill_evidence"


def test_finalize_selected_keeps_confident_historical_llm_selection() -> None:
    candidate = _historical_candidate("Manage Member Care", best=0.64, avg=0.57, support=2)
    candidate["weighted_support_count"] = 0.7

    selected = _selected("Manage Member Care")
    selected["confidence"] = 0.84

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[selected],
        llm_candidates=[candidate],
    )

    assert dropped == []
    assert rescued_confirmed == []
    assert rescued == []
    assert [row["entity_name"] for row in filtered_llm] == ["Manage Member Care"]
    assert [row["entity_name"] for row in final_selected] == ["Manage Member Care"]


def test_finalize_selected_keeps_seventy_confidence_historical_llm_selection() -> None:
    candidate = _historical_candidate("Ensure Payment Integrity", best=0.50, avg=0.40, support=1)
    candidate["direct_count"] = 0
    candidate["implied_count"] = 1
    candidate["weighted_support_count"] = 0.1

    selected = _selected("Ensure Payment Integrity")
    selected["confidence"] = 0.70

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[selected],
        llm_candidates=[candidate],
    )

    assert dropped == []
    assert rescued_confirmed == []
    assert rescued == []
    assert [row["entity_name"] for row in filtered_llm] == ["Ensure Payment Integrity"]
    assert [row["entity_name"] for row in final_selected] == ["Ensure Payment Integrity"]


def test_finalize_selected_still_drops_lower_confidence_weak_historical_selection() -> None:
    candidate = _historical_candidate("Receive Care", best=0.64, avg=0.57, support=2)
    candidate["weighted_support_count"] = 0.7

    selected = _selected("Receive Care")
    selected["confidence"] = 0.60

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[selected],
        llm_candidates=[candidate],
    )

    assert final_selected == []
    assert filtered_llm == []
    assert rescued_confirmed == []
    assert rescued == []
    assert dropped[0]["entity_name"] == "Receive Care"


def test_finalize_selected_rescues_strong_historical_gap_fill_miss() -> None:
    strong_candidate = _historical_candidate(
        "Ensure Payment Integrity",
        best=0.721,
        avg=0.62,
        support=3,
    )

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[strong_candidate],
    )

    assert filtered_llm == []
    assert rescued_confirmed == []
    assert dropped == []
    assert [row["entity_name"] for row in rescued] == ["Ensure Payment Integrity"]
    assert [row["entity_name"] for row in final_selected] == ["Ensure Payment Integrity"]
    assert final_selected[0]["selection_source"] == "historical_gap_fill_rescue"
    assert "recurring downstream workflow analog" in final_selected[0]["reason"]
    assert not _has_score_language(final_selected[0]["reason"])


def test_finalize_selected_rescues_moderate_repeated_historical_gap_after_source_exclusion() -> None:
    candidate = _historical_candidate(
        "Establish Provider Network",
        best=0.672,
        avg=0.58,
        support=5,
    )
    candidate["direct_count"] = 5
    candidate["implied_count"] = 0
    candidate["weighted_support_count"] = 0.78

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert filtered_llm == []
    assert rescued_confirmed == []
    assert dropped == []
    assert [row["entity_name"] for row in rescued] == ["Establish Provider Network"]
    assert [row["entity_name"] for row in final_selected] == ["Establish Provider Network"]


def test_finalize_selected_keeps_dense_single_ticket_direct_historical_selection() -> None:
    candidate = _historical_candidate(
        "Manage Member Care",
        best=0.804,
        avg=0.62,
        support=9,
    )
    candidate["direct_count"] = 9
    candidate["implied_count"] = 0
    candidate["supporting_ticket_ids"] = ["IDMT-11455"]

    final_selected, filtered_llm, _rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[_selected("Manage Member Care")],
        llm_candidates=[candidate],
    )

    assert dropped == []
    assert rescued == []
    assert [row["entity_name"] for row in filtered_llm] == ["Manage Member Care"]
    assert [row["entity_name"] for row in final_selected] == ["Manage Member Care"]


def test_finalize_selected_caps_historical_gap_fill_rescues() -> None:
    candidates = [
        _historical_candidate(f"Gap Fill {idx}", best=0.80 - idx * 0.01, avg=0.66, support=4)
        for idx in range(6)
    ]

    final_selected, _filtered_llm, rescued_confirmed, rescued, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=candidates,
        historical_gap_fill_budget=3,
    )

    assert rescued_confirmed == []
    assert len(rescued) == 3
    assert [row["entity_name"] for row in rescued] == ["Gap Fill 0", "Gap Fill 1", "Gap Fill 2"]
    assert [row["entity_name"] for row in final_selected] == ["Gap Fill 0", "Gap Fill 1", "Gap Fill 2"]


def test_finalize_selected_keeps_semantic_selected_outside_gap_fill_budget() -> None:
    semantic_selected = {
        "entity_id": "semantic-1",
        "entity_name": "Establish Product Offering",
        "confidence": 0.9,
        "reason": "Direct semantic fit",
    }
    semantic_candidate = {
        "entity_id": "semantic-1",
        "entity_name": "Establish Product Offering",
        "candidate_lane": "semantic_direct",
        "from_historical": False,
        "from_semantic": True,
        "semantic_score": 1.40,
    }
    historical_candidate = _historical_candidate("Recover Overpayment", best=0.73, avg=0.63)

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[semantic_selected],
        llm_candidates=[semantic_candidate, historical_candidate],
        historical_gap_fill_budget=1,
    )

    assert dropped == []
    assert rescued_confirmed == []
    assert [row["entity_name"] for row in filtered_llm] == ["Establish Product Offering"]
    assert [row["entity_name"] for row in rescued] == ["Recover Overpayment"]
    assert [row["entity_name"] for row in final_selected] == [
        "Establish Product Offering",
        "Recover Overpayment",
    ]


def test_finalize_selected_rewrites_score_based_llm_reason() -> None:
    llm_selected = {
        "entity_id": "semantic-1",
        "entity_name": "Establish Product Offering",
        "confidence": 0.9,
        "reason": "Strong semantic score 1.72 with historical hits.",
    }
    semantic_candidate = {
        "entity_id": "semantic-1",
        "entity_name": "Establish Product Offering",
        "candidate_lane": "semantic_direct",
        "from_historical": False,
        "from_semantic": True,
        "semantic_score": 1.72,
        "description": "Creating and launching new products for the market.",
    }

    final_selected, filtered_llm, _rescued_confirmed, _rescued, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[llm_selected],
        llm_candidates=[semantic_candidate],
    )

    assert filtered_llm[0]["reason"] == (
        "Selected because the idea card aligns to creating and launching new products for the market."
    )
    assert final_selected[0]["reason"] == filtered_llm[0]["reason"]
    assert not _has_score_language(final_selected[0]["reason"])


def test_finalize_selected_drops_low_score_semantic_direct_llm_selection() -> None:
    llm_selected = _selected("Adjacent Semantic Candidate")
    semantic_candidate = {
        "entity_id": "semantic-low",
        "entity_name": "Adjacent Semantic Candidate",
        "candidate_lane": "semantic_direct",
        "from_historical": False,
        "from_semantic": True,
        "semantic_score": 1.12,
    }

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[llm_selected],
        llm_candidates=[semantic_candidate],
    )

    assert final_selected == []
    assert filtered_llm == []
    assert rescued_confirmed == []
    assert rescued == []
    assert dropped == []


def test_finalize_selected_keeps_clear_semantic_direct_llm_selection() -> None:
    llm_selected = _selected("Clear Semantic Candidate")
    semantic_candidate = {
        "entity_id": "semantic-clear",
        "entity_name": "Clear Semantic Candidate",
        "candidate_lane": "semantic_direct",
        "from_historical": False,
        "from_semantic": True,
        "semantic_score": 1.31,
    }

    final_selected, filtered_llm, rescued_confirmed, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[llm_selected],
        llm_candidates=[semantic_candidate],
    )

    assert [row["entity_name"] for row in final_selected] == ["Clear Semantic Candidate"]
    assert [row["entity_name"] for row in filtered_llm] == ["Clear Semantic Candidate"]
    assert rescued_confirmed == []
    assert rescued == []
    assert dropped == []


def test_finalize_selected_rescues_repeated_confirmed_merged_miss() -> None:
    candidate = _confirmed_candidate(
        "Manage Invoice and Payment Receipt",
        semantic=1.253,
        best=0.710,
        support=8,
    )

    final_selected, filtered_llm, rescued_confirmed, rescued_gap_fill, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert filtered_llm == []
    assert rescued_gap_fill == []
    assert dropped == []
    assert [row["entity_name"] for row in rescued_confirmed] == ["Manage Invoice and Payment Receipt"]
    assert [row["entity_name"] for row in final_selected] == ["Manage Invoice and Payment Receipt"]
    assert final_selected[0]["selection_source"] == "confirmed_merged_rescue"
    assert "recurring direct workflow analog" in final_selected[0]["reason"]
    assert not _has_score_language(final_selected[0]["reason"])


def test_finalize_selected_rescues_moderate_confirmed_merged_after_source_exclusion() -> None:
    candidate = _confirmed_candidate(
        "Discover Business Insights",
        semantic=1.436,
        best=0.641,
        support=9,
        weighted=1.1,
    )

    final_selected, _filtered_llm, rescued_confirmed, rescued_gap_fill, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert rescued_gap_fill == []
    assert [row["entity_name"] for row in rescued_confirmed] == ["Discover Business Insights"]
    assert [row["entity_name"] for row in final_selected] == ["Discover Business Insights"]


def test_finalize_selected_rescues_three_hit_strong_semantic_confirmed_miss() -> None:
    candidate = _confirmed_candidate(
        "Recover Overpayment",
        semantic=1.408,
        best=0.737,
        support=3,
    )

    final_selected, _filtered_llm, rescued_confirmed, rescued_gap_fill, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert rescued_gap_fill == []
    assert [row["entity_name"] for row in rescued_confirmed] == ["Recover Overpayment"]
    assert [row["entity_name"] for row in final_selected] == ["Recover Overpayment"]


def test_finalize_selected_rescues_borderline_best_score_confirmed_miss() -> None:
    candidate = _confirmed_candidate(
        "Recover Overpayment",
        semantic=1.408,
        best=0.696,
        support=3,
    )

    final_selected, _filtered_llm, rescued_confirmed, rescued_gap_fill, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert rescued_gap_fill == []
    assert [row["entity_name"] for row in rescued_confirmed] == ["Recover Overpayment"]
    assert [row["entity_name"] for row in final_selected] == ["Recover Overpayment"]


def test_finalize_selected_does_not_rescue_two_hit_borderline_confirmed_candidate() -> None:
    candidate = _confirmed_candidate(
        "Manage Producer Operations",
        semantic=1.280,
        best=0.699,
        support=2,
    )

    final_selected, _filtered_llm, rescued_confirmed, rescued_gap_fill, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[candidate],
    )

    assert final_selected == []
    assert rescued_confirmed == []
    assert rescued_gap_fill == []
    assert dropped == []


def test_finalize_selected_drops_weak_confirmed_direct_llm_selection() -> None:
    candidate = _confirmed_candidate(
        "Appeal Decision",
        semantic=1.152,
        best=0.642,
        support=3,
        weighted=0.9,
    )

    final_selected, filtered_llm, rescued_confirmed, rescued_gap_fill, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[_selected("Appeal Decision")],
        llm_candidates=[candidate],
    )

    assert final_selected == []
    assert filtered_llm == []
    assert rescued_confirmed == []
    assert rescued_gap_fill == []
    assert dropped == []


def test_finalize_selected_keeps_confirmed_direct_with_clear_semantic_support() -> None:
    candidate = _confirmed_candidate(
        "Ensure Compliance",
        semantic=1.292,
        best=0.641,
        support=2,
        weighted=0.7,
    )

    final_selected, filtered_llm, rescued_confirmed, rescued_gap_fill, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[_selected("Ensure Compliance")],
        llm_candidates=[candidate],
    )

    assert [row["entity_name"] for row in final_selected] == ["Ensure Compliance"]
    assert [row["entity_name"] for row in filtered_llm] == ["Ensure Compliance"]
    assert rescued_confirmed == []
    assert rescued_gap_fill == []
    assert dropped == []


def _has_score_language(reason: str) -> bool:
    lower = reason.lower()
    return any(
        marker in lower
        for marker in (
            "score",
            "similarity",
            "support count",
            "historical hits",
            "best support",
            "rank",
        )
    )
