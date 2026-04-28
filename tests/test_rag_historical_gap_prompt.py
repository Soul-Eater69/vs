from __future__ import annotations

from vs_app.modules.rag.augmentation.finalizer import _split_llm_candidates
from vs_app.modules.rag.augmentation.prompt_builder import build_historical_gap_prompt


def test_split_llm_candidates_separates_historical_gap_lane() -> None:
    direct = {"entity_name": "Configure, Price, and Quote", "candidate_lane": "confirmed_direct"}
    semantic = {"entity_name": "Establish Product Offering", "candidate_lane": "semantic_direct"}
    historical = {"entity_name": "Issue Payment", "candidate_lane": "historical_recall"}

    direct_candidates, historical_candidates = _split_llm_candidates([direct, historical, semantic])

    assert direct_candidates == [direct, semantic]
    assert historical_candidates == [historical]


def test_historical_gap_prompt_explains_pattern_induced_selection() -> None:
    prompt = build_historical_gap_prompt(
        query_for_prompt="Roadmap to design and deliver tiered network and steering capabilities.",
        candidates=[
            {
                "entity_id": "vs-invoice",
                "entity_name": "Manage Invoice and Payment Receipt",
                "bucket": "historical_only",
                "candidate_lane": "historical_recall",
                "from_historical": True,
                "support_count": 10,
                "direct_count": 0,
                "implied_count": 10,
                "best_support_score": 0.867,
                "avg_support_score": 0.62,
                "historical_reasons": [
                    "[IDMT-1 / implied] recurring billing and payment receipt analog"
                ],
            }
        ],
    )

    assert "separate historical gap-fill adjudication pass" in prompt
    assert "pattern-induced" in prompt
    assert "Do not include \"Align and Execute IT Strategy\"" in prompt
    assert "Manage Invoice and Payment Receipt" in prompt
    assert "Analog evidence" in prompt
