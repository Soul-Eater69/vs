from __future__ import annotations

from vs_app.modules.rag.augmentation.finalizer import (
    _direct_selection_bounds,
    _direct_selection_max,
    _merge_selected,
    _split_llm_candidates,
)


def test_split_llm_candidates_uses_simple_lanes() -> None:
    direct, historical = _split_llm_candidates(
        [
            {"entity_name": "Both", "lane": "semantic_plus_historical"},
            {"entity_name": "Historical", "lane": "historical_only"},
            {"entity_name": "Semantic", "lane": "semantic_only"},
        ]
    )

    assert [row["entity_name"] for row in direct] == ["Both", "Semantic"]
    assert [row["entity_name"] for row in historical] == ["Historical"]


def test_direct_selection_bounds_are_prompt_guidance_only() -> None:
    assert _direct_selection_max(8) == 6
    assert _direct_selection_max(30) == 15
    assert _direct_selection_max(50) == 22
    assert _direct_selection_bounds([{"entity_name": f"VS {idx}"} for idx in range(34)]) == (3, 17)


def test_merge_selected_dedupes_without_rescue_logic() -> None:
    rows = _merge_selected(
        [],
        [
            {"entity_name": "Issue Payment", "confidence": 0.5, "reason": "first"},
            {"entity_name": "Issue Payment", "confidence": 0.8, "reason": "second"},
        ],
    )

    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.8
    assert rows[0]["reason"] == "first second"
