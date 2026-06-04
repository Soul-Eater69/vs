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


# --- summary-only alignment ------------------------------------------------------


def test_uses_summary_only_prompt_and_summary_input() -> None:
    captured: dict = {}
    _run(
        StageGenerationRequest(
            value_stream_name=VS,
            allowed_stages=ALLOWED,
            generated_summary="Generated ticket summary text.",
        ),
        captured=captured,
    )
    # summary-only prompt is selected explicitly
    assert captured["prompt_name"] == "value_stage_prediction_summary"
    # only the generated summary flows in as the ticket context
    assert captured["idea_card_text"] == "Generated ticket summary text."
    # only summary-only inputs are passed to the predictor — no leakage fields
    assert set(captured) == {
        "idea_card_text",
        "value_stream_name",
        "allowed_stages",
        "value_stream_description",
        "llm",
        "max_output_stages",
        "prompt_name",
    }


def test_generated_summary_preferred_over_idea_card_text() -> None:
    captured: dict = {}
    _run(
        StageGenerationRequest(
            value_stream_name=VS,
            allowed_stages=ALLOWED,
            generated_summary="THE SUMMARY",
            idea_card_text="raw idea card body",
        ),
        captured=captured,
    )
    assert captured["idea_card_text"] == "THE SUMMARY"


def test_support_maps_to_support_type() -> None:
    def predict_fn(**kwargs):
        return {
            "value_stream_name": VS,
            "allowed_stages": ALLOWED,
            "predicted_stages": [
                {"stage": "Account Configuration", "confidence": 0.9, "reason": "r", "support": "direct"},
                {"stage": "Generate Quote and Present to Customer", "confidence": 0.4, "reason": "r", "support": "implied"},
            ],
            "warnings": [],
        }

    result = generate_stages(
        StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED, generated_summary="s"),
        llm=object(),
        predict_fn=predict_fn,
    )
    assert [s.support_type for s in result.stages] == ["direct", "implied"]


def test_rejected_stages_never_surfaced() -> None:
    def predict_fn(**kwargs):
        return {
            "value_stream_name": VS,
            "allowed_stages": ALLOWED,
            "predicted_stages": [{"stage": "Account Configuration", "confidence": 0.9, "reason": "r"}],
            "rejected_stages": [{"stage": "Generate Quote and Present to Customer", "reason": "not supported"}],
            "warnings": [],
        }

    result = generate_stages(
        StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED, generated_summary="s"),
        llm=object(),
        predict_fn=predict_fn,
    )
    public = result.to_dict()
    # no rejection keys anywhere in the public output
    assert "rejected_stages" not in public
    for stage in public["stages"]:
        assert "rejected_stages" not in stage
        assert "rejection_reason" not in stage
        assert "rejection_rationale" not in stage


def test_request_does_not_accept_leakage_fields() -> None:
    import pytest

    for bad in ("theme_description", "business_needs", "l2_capabilities", "raw_description"):
        with pytest.raises(TypeError):
            StageGenerationRequest(value_stream_name=VS, allowed_stages=ALLOWED, **{bad: "x"})


def test_real_predictor_loads_summary_prompt_and_returns_support() -> None:
    # Exercises the real predictor (default predict_fn) so the summary prompt is
    # actually loaded and rendered. Fake structured llm — no network.
    class FakeLLM:
        def __init__(self) -> None:
            self.query = ""

        def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
            self.query = query
            return output_schema(
                picks=[
                    {
                        "stage": "Account Configuration",
                        "confidence": 0.9,
                        "reason": "Summary mentions account setup.",
                        "support": "direct",
                        "evidence_summary": "configure accounts",
                    }
                ]
            )

    llm = FakeLLM()
    result = generate_stages(
        StageGenerationRequest(
            value_stream_name=VS,
            allowed_stages=ALLOWED,
            generated_summary="Configure accounts and produce quotes.",
        ),
        llm=llm,
    )
    # prompt rendered cleanly and used the generated summary
    assert "Generated summary" in llm.query
    assert "Configure accounts and produce quotes." in llm.query
    assert [s.stage_name for s in result.stages] == ["Account Configuration"]
    assert result.stages[0].support_type == "direct"
