"""Fake-only tests for the runtime Value Stream generator.

No live Azure / LLM / Jira: a fake RAG pipeline_fn supplies a canned payload via
``ValueStreamRagService(pipeline_fn=...)`` and we assert the normalized contract.
"""

from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.value_stream_generation.generator import generate_value_streams
from vs_app.value_stream_generation.models import ValueStreamGenerationRequest
from vs_app.value_stream_generation.validators import (
    derive_support_type,
    validate_value_stream_name,
)


def _canned_payload() -> dict:
    return {
        "selected_value_streams": [
            {
                "entity_id": "VS-CPQ",
                "entity_name": "Configure, Price, and Quote",
                "confidence": 0.92,
                "reason": "Aligns to quoting and account configuration.",
                "selection_source": "llm_pick",
                "supporting_ticket_ids": ["IDMT-1001"],
            },
            {
                "entity_id": "VS-LEADS",
                "entity_name": "Manage Leads and opportunities",
                "confidence": 0.4,
                "reason": "Adjacent lead routing.",
                "selection_source": "llm_pick",
                "supporting_ticket_ids": [],
            },
            {
                "entity_id": "VS-FAKE",
                "entity_name": "Totally Fake Stream XYZ",
                "confidence": 0.5,
                "reason": "not approved",
                "selection_source": "llm_pick",
            },
            {
                "entity_id": "VS-BACKFILL",
                "entity_name": "Order to Cash",
                "confidence": 0.8,
                "reason": "low-confidence filler",
                "selection_source": "safe_backfill",
                "supporting_ticket_ids": ["IDMT-2002"],
            },
        ],
        "candidate_value_streams": [
            {
                "entity_id": "VS-CPQ",
                "entity_name": "Configure, Price, and Quote",
                "from_semantic": True,
                "from_historical": True,
                "supporting_ticket_ids": ["IDMT-1001"],
                "historical_reasons": ["Prior CPQ ticket automated quoting."],
            },
            {
                "entity_id": "VS-LEADS",
                "entity_name": "Manage Leads and opportunities",
                "from_semantic": True,
                "from_historical": False,
                "supporting_ticket_ids": [],
                "historical_reasons": [],
            },
            {
                "entity_id": "VS-BACKFILL",
                "entity_name": "Order to Cash for Group Coverage",
                "from_semantic": False,
                "from_historical": True,
                "supporting_ticket_ids": ["IDMT-2002"],
                "historical_reasons": ["Order-to-cash analog."],
            },
        ],
        "historical_source": "azure",
        "warnings": ["rag-level warning"],
    }


def _run(request, captured: dict | None = None):
    def pipeline_fn(query: str, **kwargs) -> dict:
        if captured is not None:
            captured.update(kwargs)
            captured["query"] = query
        return _canned_payload()

    service = ValueStreamRagService(pipeline_fn=pipeline_fn)
    return asyncio.run(generate_value_streams(request, service=service))


def test_normalizes_rag_payload_into_contract() -> None:
    result = _run(ValueStreamGenerationRequest(idea_card_text="quote automation idea"))

    # Non-approved name dropped; remaining three preserved in order, canonicalized.
    assert [vs.name for vs in result.value_streams] == [
        "Configure, Price, and Quote",
        "Manage Leads and Opportunities",
        "Order to Cash for Group Coverage",
    ]

    cpq = result.value_streams[0]
    assert cpq.entity_id == "VS-CPQ"
    assert cpq.support_type == "direct"
    assert cpq.confidence == 0.92
    assert cpq.rationale == "Aligns to quoting and account configuration."
    assert cpq.evidence == ["Prior CPQ ticket automated quoting."]
    assert cpq.historic_idmt_ids == ["IDMT-1001"]


def test_dropped_non_approved_name_emits_warning() -> None:
    result = _run(ValueStreamGenerationRequest(idea_card_text="idea"))
    assert any("Totally Fake Stream XYZ" in w for w in result.warnings)
    assert "rag-level warning" in result.warnings


def test_support_type_derivation_from_metadata() -> None:
    result = _run(ValueStreamGenerationRequest(idea_card_text="idea"))
    by_name = {vs.name: vs for vs in result.value_streams}

    # semantic-only, low confidence, no historical ids -> implied
    assert by_name["Manage Leads and Opportunities"].support_type == "implied"
    # safe_backfill is always implied even with ids + confidence 0.8
    assert by_name["Order to Cash for Group Coverage"].support_type == "implied"
    assert by_name["Order to Cash for Group Coverage"].historic_idmt_ids == ["IDMT-2002"]
    assert by_name["Order to Cash for Group Coverage"].evidence == ["Order-to-cash analog."]


def test_default_top_n_is_10() -> None:
    captured: dict = {}
    _run(ValueStreamGenerationRequest(idea_card_text="idea"), captured=captured)
    assert captured.get("final_output_count") == 10


def test_top_n_override_passed_and_caps_output() -> None:
    captured: dict = {}
    result = _run(
        ValueStreamGenerationRequest(idea_card_text="idea", top_n=2),
        captured=captured,
    )
    assert captured.get("final_output_count") == 2
    assert len(result.value_streams) == 2


def test_custom_instruction_warns_but_does_not_fail() -> None:
    result = _run(
        ValueStreamGenerationRequest(idea_card_text="idea", custom_instruction="prefer billing")
    )
    assert any("custom_instruction" in w for w in result.warnings)
    # generation still succeeds
    assert result.value_streams


def test_debug_carries_counts() -> None:
    result = _run(ValueStreamGenerationRequest(idea_card_text="idea"))
    assert result.debug["rag_selected_count"] == 4
    assert result.debug["generated_count"] == 3
    assert result.debug["historical_source"] == "azure"


# --- validator unit tests (pure, deterministic) ---------------------------------


def test_validate_value_stream_name_approved_and_rejected() -> None:
    assert validate_value_stream_name("Configure, Price, and Quote") == "Configure, Price, and Quote"
    assert validate_value_stream_name("Manage Leads and opportunities") == "Manage Leads and Opportunities"
    assert validate_value_stream_name("Totally Fake Stream XYZ") is None


def test_derive_support_type_rules() -> None:
    # historical support with ids -> direct
    assert derive_support_type(
        confidence=0.3, historic_idmt_ids=["IDMT-1"], from_semantic=False,
        from_historical=True, selection_source="llm_pick",
    ) == "direct"
    # both signals -> direct
    assert derive_support_type(
        confidence=0.3, historic_idmt_ids=[], from_semantic=True,
        from_historical=True, selection_source="llm_pick",
    ) == "direct"
    # high confidence -> direct
    assert derive_support_type(
        confidence=0.7, historic_idmt_ids=[], from_semantic=False,
        from_historical=False, selection_source="llm_pick",
    ) == "direct"
    # weak / semantic-only -> implied
    assert derive_support_type(
        confidence=0.4, historic_idmt_ids=[], from_semantic=True,
        from_historical=False, selection_source="llm_pick",
    ) == "implied"
    # safe_backfill always implied
    assert derive_support_type(
        confidence=0.95, historic_idmt_ids=["IDMT-1"], from_semantic=True,
        from_historical=True, selection_source="safe_backfill",
    ) == "implied"
