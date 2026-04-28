from __future__ import annotations

from vs_app.modules.rag.augmentation.finalizer import _finalize_selected


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
    }


def _selected(name: str) -> dict:
    return {
        "entity_id": name.lower().replace(" ", "-"),
        "entity_name": name,
        "confidence": 0.72,
        "reason": "LLM selected",
    }


def test_finalize_selected_drops_weak_historical_only_llm_selection() -> None:
    weak_candidate = _historical_candidate("Receive Care", best=0.691, avg=0.62, support=4)

    final_selected, filtered_llm, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[_selected("Receive Care")],
        llm_candidates=[weak_candidate],
    )

    assert final_selected == []
    assert filtered_llm == []
    assert rescued == []
    assert dropped[0]["entity_name"] == "Receive Care"
    assert dropped[0]["drop_reason"] == "weak_historical_gap_fill_evidence"


def test_finalize_selected_rescues_strong_historical_gap_fill_miss() -> None:
    strong_candidate = _historical_candidate(
        "Ensure Payment Integrity",
        best=0.721,
        avg=0.62,
        support=3,
    )

    final_selected, filtered_llm, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=[strong_candidate],
    )

    assert filtered_llm == []
    assert dropped == []
    assert [row["entity_name"] for row in rescued] == ["Ensure Payment Integrity"]
    assert [row["entity_name"] for row in final_selected] == ["Ensure Payment Integrity"]
    assert final_selected[0]["selection_source"] == "historical_gap_fill_rescue"


def test_finalize_selected_caps_historical_gap_fill_rescues() -> None:
    candidates = [
        _historical_candidate(f"Gap Fill {idx}", best=0.80 - idx * 0.01, avg=0.66, support=4)
        for idx in range(6)
    ]

    final_selected, _filtered_llm, rescued, _dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[],
        llm_candidates=candidates,
        historical_gap_fill_budget=3,
    )

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
    }
    historical_candidate = _historical_candidate("Recover Overpayment", best=0.73, avg=0.63)

    final_selected, filtered_llm, rescued, dropped = _finalize_selected(
        auto_selected=[],
        llm_selected=[semantic_selected],
        llm_candidates=[semantic_candidate, historical_candidate],
        historical_gap_fill_budget=1,
    )

    assert dropped == []
    assert [row["entity_name"] for row in filtered_llm] == ["Establish Product Offering"]
    assert [row["entity_name"] for row in rescued] == ["Recover Overpayment"]
    assert [row["entity_name"] for row in final_selected] == [
        "Establish Product Offering",
        "Recover Overpayment",
    ]
