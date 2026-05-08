from __future__ import annotations

import sys
from types import ModuleType

from vs_app.modules.rag.augmentation import finalizer
from vs_app.modules.rag.augmentation.prompt_context import build_review_pool_candidate_prompt


class _FakeResult:
    def __init__(self, selected: list[dict]) -> None:
        self._selected = selected

    def model_dump(self) -> dict:
        return {"selected_value_streams": self._selected}


def _install_generation_service(monkeypatch, fake_cls) -> None:
    module = ModuleType("vs_app.integrations.clients.generation_service")
    module.GenerationService = fake_cls
    monkeypatch.setitem(sys.modules, "vs_app.integrations.clients.generation_service", module)


def _candidate(entity_id: str, entity_name: str, lane: str, **extra) -> dict:
    row = {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "lane": lane,
        "candidate_lane": lane,
        "bucket": lane,
        "description": f"{entity_name} description",
        "candidate_status": "sent_to_llm",
    }
    row.update(extra)
    return row


def test_review_pool_finalizer_calls_llm_once_and_passes_all_lanes(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            calls.append(kwargs)
            return _FakeResult(
                [
                    {
                        "entity_id": "vs-issue",
                        "entity_name": "Issue Payment",
                        "confidence": 0.7,
                        "reason": "Payment processing is a plausible downstream review item.",
                    }
                ]
            )

    _install_generation_service(monkeypatch, FakeGenerationService)
    candidates = [
        _candidate(
            "vs-issue",
            "Issue Payment",
            "semantic_plus_historical",
            from_semantic=True,
            from_historical=True,
            semantic_score=1.5,
            supporting_ticket_count=2,
            support_count=2,
            historical_reasons=["[IDMT-1 / direct] Similar payment work."],
        ),
        _candidate("vs-sem", "Manage Leads", "semantic_only", from_semantic=True, semantic_score=1.2),
        _candidate(
            "vs-hist",
            "Receive Care",
            "historical_only",
            from_historical=True,
            supporting_ticket_count=1,
            support_count=1,
            historical_reasons=["[IDMT-2 / implied] Similar care workflow."],
        ),
    ]

    result = finalizer.generate_review_pool_value_streams(
        query_for_prompt="idea card",
        llm_candidates=candidates,
        final_output_count=20,
    )

    assert len(calls) == 1
    prompt = calls[0]["query"]
    assert "Issue Payment" in prompt
    assert "Manage Leads" in prompt
    assert "Receive Care" in prompt
    assert result["raw_response"]["selection_budget"]["max_select"] == 3
    assert result["raw_response"]["single_review_pool_pass"]["candidate_count"] == 3


def test_review_pool_finalizer_removes_invented_output(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult(
                [
                    {
                        "entity_id": "missing",
                        "entity_name": "Invented Stream",
                        "confidence": 0.9,
                        "reason": "Not in candidates.",
                    }
                ]
            )

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_review_pool_value_streams(
        query_for_prompt="idea card",
        llm_candidates=[
            _candidate("vs-1", "Issue Payment", "semantic_only", from_semantic=True),
        ],
        final_output_count=5,
    )

    assert result["selected_value_streams"] == []
    assert result["raw_response"]["single_review_pool_pass"]["rejected_candidates"][0]["entity_name"] == "Issue Payment"


def test_review_pool_missed_strong_candidates_are_debug_only(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult([])

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_review_pool_value_streams(
        query_for_prompt="idea card",
        llm_candidates=[
            _candidate(
                "vs-strong",
                "Issue Payment",
                "semantic_plus_historical",
                from_semantic=True,
                from_historical=True,
                semantic_score=1.4,
                supporting_ticket_count=6,
                support_count=6,
                best_support_score=0.8,
            ),
        ],
        final_output_count=5,
    )

    assert result["selected_value_streams"] == []
    missed = result["raw_response"]["missed_strong_candidates"]
    assert [row["entity_name"] for row in missed] == ["Issue Payment"]


def test_review_pool_prompt_stays_compact() -> None:
    candidates = [
        _candidate(
            f"vs-{idx}",
            f"Value Stream {idx}",
            "semantic_plus_historical" if idx % 3 == 0 else "semantic_only",
            from_semantic=True,
            from_historical=idx % 3 == 0,
            semantic_score=1.4,
            supporting_ticket_count=3,
            support_count=3,
            direct_count=1,
            implied_count=2,
            best_support_score=0.8,
            avg_support_score=0.7,
            weighted_support=1.2,
            supporting_ticket_ids=["H1", "H2", "H3", "H4"],
            historical_reasons=[
                "[IDMT-1 / direct] This is a long but compact analog reason about a similar workflow."
                * 3,
                "[IDMT-2 / implied] This is another analog reason about downstream work." * 3,
                "[IDMT-3 / implied] Extra evidence should be trimmed." * 3,
            ],
            description="Detailed candidate description " * 20,
        )
        for idx in range(25)
    ]

    prompt = build_review_pool_candidate_prompt(
        query_for_prompt="x" * 5000,
        candidates=candidates,
        final_output_count=20,
        prompt_budget={
            "idea_card_prompt_chars": 2200,
            "candidate_description_chars": 160,
            "analogs_per_candidate": 2,
            "analog_chars": 140,
            "historical_ticket_ids_per_candidate": 3,
        },
    )

    assert len(prompt) < 14000
