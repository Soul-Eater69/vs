from __future__ import annotations

import sys
from types import ModuleType

from vs_app.modules.rag.augmentation import finalizer


class _FakeResult:
    def __init__(self, selected: list[dict]) -> None:
        self._selected = selected

    def model_dump(self) -> dict:
        return {"selected_value_streams": self._selected}


def _install_generation_service(monkeypatch, fake_cls) -> None:
    module = ModuleType("vs_app.integrations.clients.generation_service")
    module.GenerationService = fake_cls
    monkeypatch.setitem(sys.modules, "vs_app.integrations.clients.generation_service", module)


def test_auto_selected_input_is_ignored_for_final_output(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult([])

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_value_streams(
        query_for_prompt="query",
        llm_candidates=[],
        auto_selected=[{"entity_name": "Auto Selected", "confidence": 1.0, "reason": "old"}],
    )

    assert result["selected_value_streams"] == []
    assert result["raw_response"]["auto_selected_ignored"][0]["entity_name"] == "Auto Selected"
    assert result["rescued_confirmed_merged_value_streams"] == []
    assert result["rescued_historical_gap_fill_value_streams"] == []
    assert result["dropped_historical_gap_fill_value_streams"] == []


def test_candidate_not_selected_by_llm_is_not_rescued(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult([])

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_value_streams(
        query_for_prompt="query",
        llm_candidates=[
            {
                "entity_id": "vs-1",
                "entity_name": "Issue Payment",
                "lane": "semantic_plus_historical",
                "from_semantic": True,
                "from_historical": True,
                "semantic_score": 2.0,
                "supporting_ticket_count": 5,
                "support_count": 5,
                "best_support_score": 0.9,
            }
        ],
    )

    assert result["selected_value_streams"] == []
    assert result["raw_response"]["direct_pass"]["rejected_candidates"][0]["entity_name"] == "Issue Payment"


def test_historical_only_selected_by_llm_remains_selected(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult(
                [
                    {
                        "entity_id": "vs-h",
                        "entity_name": "Receive Care",
                        "confidence": 0.7,
                        "reason": "Similar prior tickets imply member care delivery.",
                    }
                ]
            )

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_value_streams(
        query_for_prompt="query",
        llm_candidates=[
            {
                "entity_id": "vs-h",
                "entity_name": "Receive Care",
                "lane": "historical_only",
                "from_historical": True,
                "supporting_ticket_count": 2,
                "support_count": 2,
            }
        ],
    )

    assert [row["entity_name"] for row in result["selected_value_streams"]] == ["Receive Care"]
    assert result["raw_response"]["historical_gap_pass"]["rejected_candidates"] == []


def test_finalizer_dedupes_selected_candidates(monkeypatch) -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return _FakeResult(
                [
                    {
                        "entity_id": "vs-1",
                        "entity_name": "Issue Payment",
                        "confidence": 0.5,
                        "reason": "first",
                    },
                    {
                        "entity_id": "vs-1",
                        "entity_name": "Issue Payment",
                        "confidence": 0.8,
                        "reason": "second",
                    },
                ]
            )

    _install_generation_service(monkeypatch, FakeGenerationService)

    result = finalizer.generate_value_streams(
        query_for_prompt="query",
        llm_candidates=[
            {
                "entity_id": "vs-1",
                "entity_name": "Issue Payment",
                "lane": "semantic_only",
                "from_semantic": True,
            }
        ],
    )

    assert len(result["selected_value_streams"]) == 1
    assert result["selected_value_streams"][0]["confidence"] == 0.8
