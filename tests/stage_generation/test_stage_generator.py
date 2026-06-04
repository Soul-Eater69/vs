"""Fake-only tests for the runtime stage generator.

No live Azure / Jira / LLM: a fake ``predict_fn`` supplies a canned prediction
payload, and one test exercises the real default predictor with ``llm=None``
(which returns deterministically without any network call).
"""

from __future__ import annotations

from vs_app.stage_generation.generator import generate_stages
from vs_app.stage_generation.models import GeneratedStage, StageGenerationRequest
from vs_app.stage_generation.validators import match_allowed_stage

VS = "Configure, Price, and Quote"
ALLOWED = ["Account Configuration", "Generate Quote and Present to Customer"]


def _canned_prediction() -> dict:
    return {
        "value_stream_name": VS,
        "allowed_stages": ALLOWED,
        "predicted_stages": [
            {"stage": "Account Configuration", "confidence": 0.91, "reason": "Set up accounts."},
            # lowercase spelling -> canonicalized to the allowed spelling
            {"stage": "generate quote and present to customer", "confidence": 0.4, "reason": "Quote it."},
            # invented stage -> dropped with warning
            {"stage": "Totally Invented Stage", "confidence": 0.8, "reason": "nope"},
            # duplicate of the first -> deduped
            {"stage": "Account Configuration", "confidence": 0.5, "reason": "dup"},
        ],
        "warnings": ["predictor-level warning"],
        "raw_response": "",
    }


def _run(request: StageGenerationRequest, captured: dict | None = None):
    def predict_fn(**kwargs) -> dict:
        if captured is not None:
            captured.update(kwargs)
        return _canned_prediction()

    return generate_stages(request, llm=object(), predict_fn=predict_fn)


def test_normalizes_prediction_into_contract() -> None:
    result = _run(StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED, idea_card_text="idea"))

    assert result.value_stream_name == VS
    assert [s.stage_name for s in result.stages] == [
        "Account Configuration",
        "Generate Quote and Present to Customer",
    ]
    first = result.stages[0]
    assert first.value_stream_name == VS
    assert first.rationale == "Set up accounts."
    assert first.confidence == 0.91
    assert first.stage_id == ""
    assert first.support_type == ""


def test_public_contract_exact_keys_and_confidence_scaling() -> None:
    result = _run(StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED))
    public = result.stages[0].to_dict()
    assert set(public) == {
        "stage_id",
        "stage_name",
        "value_stream_name",
        "rationale",
        "confidence_score",
        "support_type",
    }
    assert public == {
        "stage_id": "",
        "stage_name": "Account Configuration",
        "value_stream_name": VS,
        "rationale": "Set up accounts.",
        "confidence_score": 91,
        "support_type": "",
    }
    assert isinstance(public["confidence_score"], int)


def test_invented_stage_dropped_with_warning() -> None:
    result = _run(StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED))
    assert any("Totally Invented Stage" in w for w in result.warnings)
    assert "predictor-level warning" in result.warnings
    assert "Totally Invented Stage" not in [s.stage_name for s in result.stages]


def test_dedupes_stages_by_name() -> None:
    result = _run(StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED))
    names = [s.stage_name for s in result.stages]
    assert names.count("Account Configuration") == 1


def test_request_fields_passed_to_predictor() -> None:
    captured: dict = {}
    _run(
        StageGenerationRequest(
            value_stream_name=VS,
            allowed_stages=ALLOWED,
            idea_card_text="my idea",
            value_stream_description="desc",
            max_output_stages=3,
        ),
        captured=captured,
    )
    assert captured["value_stream_name"] == VS
    assert captured["allowed_stages"] == ALLOWED
    assert captured["idea_card_text"] == "my idea"
    assert captured["value_stream_description"] == "desc"
    assert captured["max_output_stages"] == 3


def test_result_to_dict_shape() -> None:
    result = _run(StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED))
    payload = result.to_dict()
    assert set(payload) == {"value_stream_name", "stages", "warnings", "debug"}
    assert payload["debug"]["predicted_count"] == 4
    assert payload["debug"]["generated_count"] == 2


def test_confidence_score_scaling_and_clamping() -> None:
    assert GeneratedStage("s", VS, "r", 0.0).to_dict()["confidence_score"] == 0
    assert GeneratedStage("s", VS, "r", 0.4).to_dict()["confidence_score"] == 40
    assert GeneratedStage("s", VS, "r", 1.0).to_dict()["confidence_score"] == 100
    assert GeneratedStage("s", VS, "r", 1.5).to_dict()["confidence_score"] == 100


def test_real_predictor_with_no_llm_is_safe_and_empty() -> None:
    # Default predict_fn + llm=None returns deterministically, no network call.
    result = generate_stages(
        StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED, idea_card_text="idea"),
        llm=None,
    )
    assert result.stages == []
    assert any("no llm provided" in w for w in result.warnings)


def test_match_allowed_stage_unit() -> None:
    assert match_allowed_stage("account configuration", ALLOWED) == "Account Configuration"
    assert match_allowed_stage("  Account   Configuration ", ALLOWED) == "Account Configuration"
    assert match_allowed_stage("Invented", ALLOWED) is None
    assert match_allowed_stage("", ALLOWED) is None
