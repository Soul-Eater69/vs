from __future__ import annotations

from vs_app.modules.stages.finalizer import (
    StageSelectionResult,
    _format_candidate_stages,
    select_stages_with_llm,
)
from vs_app.modules.stages.pipeline import predict_stages


def _stages() -> list[dict]:
    return [
        {"stage_id": "VSS1", "stage_sequence": 1, "stage_name": "First Stage"},
        {"stage_id": "VSS2", "stage_sequence": 2, "stage_name": "Second Stage"},
        {"stage_id": "VSS3", "stage_sequence": 3, "stage_name": "Third Stage"},
        {"stage_id": "VSS4", "stage_sequence": 4, "stage_name": "Fourth Stage"},
    ]


def test_predict_stages_does_not_run_without_selected_value_streams() -> None:
    called = False

    def catalog_fn(**kwargs):
        nonlocal called
        called = True
        return []

    result = predict_stages(
        condensed_idea_card="summary",
        selected_value_streams=[],
        catalog_fn=catalog_fn,
    )

    assert result == {"stage_predictions": [], "stage_candidate_debug": []}
    assert called is False


def test_predict_stages_without_catalog_stages_returns_broad_or_unclear() -> None:
    result = predict_stages(
        condensed_idea_card="summary",
        selected_value_streams=[{"entity_id": "VSR1", "entity_name": "Example Stream"}],
        catalog_fn=lambda **kwargs: [],
    )

    assert result["stage_predictions"][0]["stage_scope"] == "broad_or_unclear"
    assert result["stage_predictions"][0]["selected_stages"] == []
    assert result["stage_candidate_debug"][0]["candidate_stage_count"] == 0


def test_predict_stages_with_catalog_stages_returns_prediction() -> None:
    result = predict_stages(
        condensed_idea_card="summary",
        selected_value_streams=[{"entity_id": "VSR1", "entity_name": "Example Stream"}],
        catalog_fn=lambda **kwargs: _stages()[:2],
        finalizer_fn=lambda **kwargs: {
            "value_stream_id": "VSR1",
            "value_stream_name": "Example Stream",
            "stage_scope": "specific_stages",
            "selected_stages": [{"stage_id": "VSS1", "stage_name": "First Stage"}],
            "reason": "clear match",
        },
    )

    assert result["stage_predictions"][0]["selected_stages"][0]["stage_id"] == "VSS1"


def test_stage_finalizer_drops_unknown_and_caps_to_three_stages() -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return StageSelectionResult(
                value_stream_id="VSR1",
                value_stream_name="Example Stream",
                stage_scope="specific_stages",
                selected_stages=[
                    {"stage_id": "UNKNOWN", "confidence": 1.0, "reason": "bad"},
                    {"stage_id": "VSS1", "confidence": 1.2, "reason": "one"},
                    {"stage_id": "VSS2", "confidence": 0.8, "reason": "two"},
                    {"stage_id": "VSS3", "confidence": 0.7, "reason": "three"},
                    {"stage_id": "VSS4", "confidence": 0.6, "reason": "four"},
                ],
                reason="specific",
            )

    result = select_stages_with_llm(
        condensed_idea_card="summary",
        selected_value_stream={"entity_id": "VSR1", "entity_name": "Example Stream"},
        candidate_stages=_stages(),
        generation_service=FakeGenerationService(),
    )

    assert result["stage_scope"] == "specific_stages"
    assert [stage["stage_id"] for stage in result["selected_stages"]] == ["VSS1", "VSS2", "VSS3"]
    assert result["selected_stages"][0]["stage_name"] == "First Stage"
    assert result["selected_stages"][0]["confidence"] == 1.0


def test_stage_finalizer_unknown_only_becomes_broad_or_unclear() -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return StageSelectionResult(
                stage_scope="specific_stages",
                selected_stages=[{"stage_id": "UNKNOWN", "confidence": 1.0}],
            )

    result = select_stages_with_llm(
        condensed_idea_card="summary",
        selected_value_stream={"entity_id": "VSR1", "entity_name": "Example Stream"},
        candidate_stages=_stages(),
        generation_service=FakeGenerationService(),
    )

    assert result["stage_scope"] == "broad_or_unclear"
    assert result["selected_stages"] == []


def test_stage_finalizer_uncertain_entire_stream_becomes_broad_or_unclear() -> None:
    class FakeGenerationService:
        def generate_structured(self, **kwargs):
            return StageSelectionResult(
                stage_scope="entire_value_stream",
                selected_stages=[],
                reason="The idea is broad and no specific stage is described.",
            )

    result = select_stages_with_llm(
        condensed_idea_card="summary",
        selected_value_stream={"entity_id": "VSR1", "entity_name": "Example Stream"},
        candidate_stages=_stages(),
        generation_service=FakeGenerationService(),
    )

    assert result["stage_scope"] == "broad_or_unclear"
    assert result["selected_stages"] == []


def test_stage_candidate_formatting_is_compact_and_omits_stakeholders() -> None:
    long_text = " ".join(["detail"] * 120)

    formatted = _format_candidate_stages(
        [
            {
                "stage_id": "VSS1",
                "stage_sequence": 1,
                "stage_name": "First Stage",
                "stage_description": long_text,
                "stage_entrance_criteria": long_text,
                "stage_exit_criteria": long_text,
                "stage_value_items": long_text,
                "stage_stakeholders": "Generic Stakeholder Group",
            }
        ]
    )

    assert "Stage ID: VSS1" in formatted
    assert "Description:" in formatted
    assert "..." in formatted
    assert "Stakeholders" not in formatted
    assert "Generic Stakeholder Group" not in formatted
