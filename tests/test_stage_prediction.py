from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts.evaluate_stage_prediction_batch import dataset_idea_card_text, evaluate_prediction_row
from vs_app.modules.stages.stage_prediction import (
    allowed_stage_payload,
    build_stage_prediction_task,
    parse_stage_prediction_response,
    predict_stages_for_predicted_value_streams,
)


def _catalog() -> dict:
    return {
        "Manage Leads and Opportunities": {
            "value_stream_id": "VSR-LEADS",
            "description": "Manage prospective customer leads and sales opportunities.",
            "stages": [
                {
                    "name": "Perform Outreach to Leads and Prospects",
                    "id": "VSS-1",
                    "description": "Contact prioritized leads and prospects.",
                    "entrance_criteria": "Lead is ready for outreach.",
                    "exit_criteria": "Outreach is completed.",
                    "value_items": "Lead contact record",
                    "stakeholders": "Sales",
                    "aliases": [],
                }
            ],
        }
    }


def _ticket_context() -> dict:
    return {
        "ticket_id": "IDMT-1",
        "summary": "Lead outreach",
        "description": "Improve outreach for prioritized leads.",
        "idea_card_text": "The idea card says perform outreach to prospects.",
    }


def _predicted_vs() -> list[dict]:
    return [
        {
            "name": "Manage Leads and Opportunities",
            "id": "VSR-LEADS",
            "confidence": 0.87,
            "reason": "Ticket is about lead outreach.",
        }
    ]


class _FakeLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.messages: list[dict] = []

    def invoke(self, messages: list[dict]) -> SimpleNamespace:
        self.messages = messages
        return SimpleNamespace(content=json.dumps(self.payload))


def test_stage_prediction_prompt_rendering_contains_context_and_schema() -> None:
    task = build_stage_prediction_task(
        ticket_context=_ticket_context(),
        predicted_value_stream=_predicted_vs()[0],
        stage_catalog=_catalog(),
    )

    prompt = task["user_prompt"]
    assert "IDMT-1" in prompt
    assert "perform outreach to prospects" in prompt
    assert "Manage Leads and Opportunities" in prompt
    assert "Perform Outreach to Leads and Prospects" in prompt
    assert '"predicted_stages"' in prompt
    assert '"rejected_stages"' not in prompt


def test_allowed_stage_payload_supports_catalog_stage_name_variants() -> None:
    payload = json.loads(
        allowed_stage_payload(
            [
                {
                    "stage_id": "VSS-1",
                    "value_stream_stage_name": "Perform Outreach to Leads and Prospects",
                    "value_stream_stage_description": "Contact leads.",
                    "value_stream_stage_entrance_criteria": "Lead is ready.",
                    "value_stream_stage_exit_criteria": "Outreach completed.",
                    "value_stream_stage_value_items": "Lead contact record",
                    "value_stream_stage_stakeholders": "Sales",
                }
            ]
        )
    )

    assert payload[0]["stage_name"] == "Perform Outreach to Leads and Prospects"
    assert payload[0]["stage_description"] == "Contact leads."
    assert payload[0]["stage_entrance_criteria"] == "Lead is ready."
    assert payload[0]["stage_exit_criteria"] == "Outreach completed."
    assert payload[0]["stage_value_items"] == "Lead contact record"
    assert payload[0]["stage_stakeholders"] == "Sales"


def test_stage_prediction_parser_accepts_valid_json() -> None:
    parsed = parse_stage_prediction_response(
        json.dumps(
            {
                "ticket_id": "IDMT-1",
                "value_stream_name": "Manage Leads and Opportunities",
                "predicted_stages": [
                    {
                        "stage_name": "Perform Outreach to Leads and Prospects",
                        "confidence": 0.9,
                        "support": "direct",
                        "reason": "The idea card mentions perform outreach.",
                        "evidence_summary": "perform outreach",
                    }
                ],
                "rejected_stages": [],
                "warnings": [],
            }
        )
    )

    assert parsed["predicted_stages"][0]["stage_name"] == "Perform Outreach to Leads and Prospects"


@pytest.mark.anyio
async def test_non_approved_stage_is_dropped() -> None:
    llm = _FakeLLM(
        {
            "predicted_stages": [
                {
                    "stage_name": "Pre-Sale Analytics",
                    "confidence": 0.8,
                    "support": "implied",
                    "reason": "Analytics are mentioned.",
                }
            ],
            "rejected_stages": [],
            "warnings": [],
        }
    )

    result = await predict_stages_for_predicted_value_streams(
        llm=llm,
        ticket_context=_ticket_context(),
        predicted_value_streams=_predicted_vs(),
        stage_catalog=_catalog(),
    )

    assert result["predictions_by_value_stream"]["Manage Leads and Opportunities"] == []
    warnings = result["value_stream_predictions"][0]["warnings"]
    assert any("dropped non-approved stage" in warning for warning in warnings)
    assert "rejected_stages" not in result["value_stream_predictions"][0]
    assert "rejected_stages" not in result["value_stream_predictions"][0]["raw_response"]


@pytest.mark.anyio
async def test_duplicate_predicted_stages_collapse() -> None:
    llm = _FakeLLM(
        {
            "predicted_stages": [
                {
                    "stage_name": "Perform Outreach to Leads and Prospects",
                    "confidence": 0.8,
                    "support": "direct",
                    "reason": "Outreach.",
                },
                {
                    "stage_name": "Perform Outreach to Leads and Prospects",
                    "confidence": 0.9,
                    "support": "direct",
                    "reason": "Prospects.",
                },
            ],
            "rejected_stages": [],
            "warnings": [],
        }
    )

    result = await predict_stages_for_predicted_value_streams(
        llm=llm,
        ticket_context=_ticket_context(),
        predicted_value_streams=_predicted_vs(),
        stage_catalog=_catalog(),
    )

    selected = result["value_stream_predictions"][0]["selected_stages"]
    assert len(selected) == 1
    assert selected[0]["canonical_stage"] == "Perform Outreach to Leads and Prospects"
    assert selected[0]["confidence"] == 0.9


@pytest.mark.anyio
async def test_stage_prediction_does_not_use_gt_or_child_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    from vs_app.modules.stages import stage_ground_truth

    async def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("stage prediction must not fetch child Epics")

    monkeypatch.setattr(stage_ground_truth, "fetch_direct_child_epics", forbidden)
    llm = _FakeLLM(
        {
            "predicted_stages": [
                {"stage_name": "Perform Outreach to Leads and Prospects", "confidence": 0.9}
            ],
            "rejected_stages": [],
            "warnings": [],
        }
    )

    result = await predict_stages_for_predicted_value_streams(
        llm=llm,
        ticket_context=_ticket_context(),
        predicted_value_streams=_predicted_vs(),
        stage_catalog=_catalog(),
    )
    prompt_text = "\n".join(message["content"] for message in llm.messages)

    assert result["predictions_by_value_stream"]["Manage Leads and Opportunities"] == [
        "Perform Outreach to Leads and Prospects"
    ]
    assert "Parent Link" not in prompt_text
    assert "childIssuesOf" not in prompt_text
    assert "verified_stages" not in prompt_text
    assert "gt_by_value_stream" not in prompt_text


def test_stage_prediction_metrics_perfect_match() -> None:
    row = evaluate_prediction_row(
        ticket_id="IDMT-1",
        value_stream_name="Manage Leads and Opportunities",
        gt_ticket={
            "gt_by_value_stream": {
                "Manage Leads and Opportunities": ["Perform Outreach to Leads and Prospects"]
            }
        },
        prediction={
            "predictions_by_value_stream": {
                "Manage Leads and Opportunities": ["Perform Outreach to Leads and Prospects"]
            }
        },
    )

    assert row["precision"] == 1.0
    assert row["recall"] == 1.0
    assert row["f1"] == 1.0


def test_dataset_idea_card_text_context_modes() -> None:
    idea_card = {
        "idea_card_text": "raw idea",
        "attachment_text": "attachment",
        "extracted_text": "extracted",
        "generated_summary": "summary",
    }

    assert dataset_idea_card_text(idea_card, context_mode="raw") == "raw idea\n\nattachment\n\nextracted"
    assert dataset_idea_card_text(idea_card, context_mode="full") == (
        "raw idea\n\nattachment\n\nextracted\n\nsummary"
    )
    assert dataset_idea_card_text(idea_card, context_mode="summary") == "summary"
    assert dataset_idea_card_text({"generated_summary": "fallback"}, context_mode="raw") == "fallback"


def test_stage_prediction_metrics_missed_stage() -> None:
    row = evaluate_prediction_row(
        ticket_id="IDMT-1",
        value_stream_name="Manage Leads and Opportunities",
        gt_ticket={
            "gt_by_value_stream": {
                "Manage Leads and Opportunities": ["Perform Outreach to Leads and Prospects"]
            }
        },
        prediction={"predictions_by_value_stream": {"Manage Leads and Opportunities": []}},
    )

    assert row["fn"] == ["Perform Outreach to Leads and Prospects"]
    assert row["recall"] == 0.0


def test_stage_prediction_metrics_extra_stage() -> None:
    row = evaluate_prediction_row(
        ticket_id="IDMT-1",
        value_stream_name="Manage Leads and Opportunities",
        gt_ticket={"gt_by_value_stream": {"Manage Leads and Opportunities": []}},
        prediction={
            "predictions_by_value_stream": {
                "Manage Leads and Opportunities": ["Perform Outreach to Leads and Prospects"]
            }
        },
    )

    assert row["fp"] == ["Perform Outreach to Leads and Prospects"]
    assert row["precision"] == 0.0
