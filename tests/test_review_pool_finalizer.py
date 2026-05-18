from __future__ import annotations

import sys
from types import ModuleType

from vs_app.modules.rag.augmentation import finalizer
from vs_app.modules.rag.augmentation.prompt_context import build_review_pool_candidate_prompt
from vs_app.modules.prompts.loader import build_review_pool_selection_system_prompt


class _FakeResult:
    def __init__(self, selected: list[dict]) -> None:
        self._selected = selected

    def model_dump(self) -> dict:
        return {"picks": self._selected}


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


def test_review_pool_finalizer_ignores_invented_output_without_exact_fill(monkeypatch) -> None:
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
    assert [row["entity_name"] for row in result["raw_response"]["single_review_pool_pass"]["rejected_candidates"]] == [
        "Issue Payment"
    ]


def test_review_pool_safe_backfills_strong_candidate_when_llm_returns_none(monkeypatch) -> None:
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

    assert [row["entity_name"] for row in result["selected_value_streams"]] == ["Issue Payment"]
    assert result["selected_value_streams"][0]["selection_source"] == "safe_backfill"
    missed = result["raw_response"]["missed_strong_candidates"]
    assert missed == []


def test_review_pool_does_not_exact_fill_to_requested_count(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult(
                [
                    {
                        "entity_id": "vs-1",
                        "confidence": 0.8,
                        "reason": "First candidate is clearly supported.",
                    }
                ]
            )

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_review_pool_value_streams(
        query_for_prompt="idea card",
        llm_candidates=[
            _candidate("vs-1", "First Stream", "semantic_only", from_semantic=True),
            _candidate("vs-2", "Second Stream", "semantic_only", from_semantic=True),
            _candidate("vs-3", "Third Stream", "historical_only", from_historical=True),
        ],
        final_output_count=3,
    )

    assert len(result["selected_value_streams"]) == 1
    assert [row["selection_source"] for row in result["selected_value_streams"]] == [
        "llm_pick",
    ]
    assert result["raw_response"]["single_review_pool_pass"]["selected_value_streams"] == (
        result["selected_value_streams"]
    )


def test_review_pool_does_not_fill_plain_candidates_when_request_exceeds_candidates(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult([])

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_review_pool_value_streams(
        query_for_prompt="idea card",
        llm_candidates=[
            _candidate("vs-1", "First Stream", "semantic_only", from_semantic=True),
            _candidate("vs-2", "Second Stream", "historical_only", from_historical=True),
        ],
        final_output_count=5,
    )

    assert result["selected_value_streams"] == []
    assert result["raw_response"]["requested_final_output_count"] == 5


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


def test_review_pool_system_prompt_is_recall_friendly() -> None:
    prompt = build_review_pool_selection_system_prompt(max_select=15)

    assert "HOW TO READ THE CANDIDATE BLOCKS" in prompt
    assert "SELECTION POLICY" in prompt
    assert "Prefer recall over precision" in prompt
    assert "You may return fewer than 15" in prompt
    assert "HISTORICAL ANALOG STATUS" not in prompt
    assert "Return exactly" not in prompt
    assert "selection_type" not in prompt
    assert len(prompt) < 15000
