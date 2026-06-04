"""Fake-only tests for L2 / L3 capability generation.

No live Azure / Jira / LLM: a fake LLM keyed on the requested output schema
drives both generators. Also asserts no historic stage context reaches the
prompt.
"""

from __future__ import annotations

from typing import Any

from vs_app.theme_generation.capabilities import (
    generate_l2_capabilities,
    generate_l3_capabilities,
)
from vs_app.theme_generation.models import GeneratedL2Capability

VS = "Configure, Price, and Quote"
SELECTED_STAGES = [{"stage": "Account Configuration", "confidence": 0.9, "reason": "set up"}]


class FakeLLM:
    """Returns structured L2/L3 results; records the last prompt seen."""

    def __init__(self) -> None:
        self.last_query = ""

    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        self.last_query = query
        name = output_schema.__name__
        if name == "L2CapabilityResult":
            return output_schema(
                capabilities=[
                    {"capability_name": "Quote Management", "rationale": "Manage quotes.", "confidence": 0.9},
                    {"capability_name": "Account Setup", "rationale": "Configure accounts.", "confidence": 0.4},
                ]
            )
        if name == "L3CapabilityResult":
            return output_schema(
                capabilities=[
                    {
                        "capability_name": "Quote Versioning",
                        "parent_l2_capability_name": "Quote Management",
                        "rationale": "Track quote versions.",
                        "confidence": 0.8,
                    },
                    # unknown parent -> dropped with warning
                    {
                        "capability_name": "Orphan Cap",
                        "parent_l2_capability_name": "Nonexistent L2",
                        "rationale": "x",
                        "confidence": 0.5,
                    },
                ]
            )
        return output_schema()


class MalformedLLM:
    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        raise ValueError("gateway boom")


def _l2_args(**overrides) -> dict:
    args = dict(
        idea_context="quote automation idea",
        value_stream_name=VS,
        selected_stages=SELECTED_STAGES,
        theme_description="A CPQ theme.",
        business_needs="Faster quoting.",
    )
    args.update(overrides)
    return args


def test_l2_maps_structured_output() -> None:
    caps, warnings = generate_l2_capabilities(**_l2_args(), llm=FakeLLM())
    assert [c.capability_name for c in caps] == ["Quote Management", "Account Setup"]
    first = caps[0]
    assert first.capability_id == ""
    assert first.rationale == "Manage quotes."
    assert first.to_dict() == {
        "capability_id": "",
        "capability_name": "Quote Management",
        "rationale": "Manage quotes.",
        "confidence_score": 90,
    }
    assert warnings == []


def test_l3_maps_structured_output_with_parent() -> None:
    l2 = [
        GeneratedL2Capability("Quote Management", "Manage quotes.", 0.9),
        GeneratedL2Capability("Account Setup", "Configure accounts.", 0.4),
    ]
    caps, warnings = generate_l3_capabilities(
        idea_context="idea",
        value_stream_name=VS,
        selected_stages=SELECTED_STAGES,
        theme_description="A CPQ theme.",
        business_needs="Faster quoting.",
        l2_capabilities=l2,
        llm=FakeLLM(),
    )
    assert len(caps) == 1
    c = caps[0]
    assert c.capability_name == "Quote Versioning"
    assert c.parent_l2_capability_name == "Quote Management"
    assert c.to_dict() == {
        "capability_id": "",
        "capability_name": "Quote Versioning",
        "parent_l2_capability_name": "Quote Management",
        "rationale": "Track quote versions.",
        "confidence_score": 80,
    }
    # unknown-parent row dropped with a warning
    assert any("Nonexistent L2" in w for w in warnings)


def test_confidence_scaling_and_clamping() -> None:
    assert GeneratedL2Capability("c", "r", 0.0).to_dict()["confidence_score"] == 0
    assert GeneratedL2Capability("c", "r", 0.4).to_dict()["confidence_score"] == 40
    assert GeneratedL2Capability("c", "r", 1.0).to_dict()["confidence_score"] == 100
    assert GeneratedL2Capability("c", "r", 1.5).to_dict()["confidence_score"] == 100


def test_malformed_output_returns_empty_with_warning() -> None:
    caps, warnings = generate_l2_capabilities(**_l2_args(), llm=MalformedLLM())
    assert caps == []
    assert any("l2 capability generation failed" in w for w in warnings)


def test_no_llm_returns_empty_with_warning() -> None:
    l2_caps, l2_warn = generate_l2_capabilities(**_l2_args(), llm=None)
    assert l2_caps == []
    assert any("no llm provided" in w for w in l2_warn)

    l3_caps, l3_warn = generate_l3_capabilities(
        idea_context="i", value_stream_name=VS, selected_stages=[],
        theme_description="d", business_needs="b",
        l2_capabilities=[GeneratedL2Capability("X", "r", 0.5)], llm=None,
    )
    assert l3_caps == []
    assert any("no llm provided" in w for w in l3_warn)


def test_l3_with_no_l2_returns_empty_with_warning() -> None:
    caps, warnings = generate_l3_capabilities(
        idea_context="i", value_stream_name=VS, selected_stages=[],
        theme_description="d", business_needs="b", l2_capabilities=[], llm=FakeLLM(),
    )
    assert caps == []
    assert any("no l2 capabilities available" in w for w in warnings)


def test_no_historic_stage_context_in_prompt() -> None:
    # Examples may carry stages upstream; the prompt must never include them.
    llm = FakeLLM()
    examples = [
        {
            "value_stream_name": VS,
            "theme_description": "Prior theme.",
            "business_needs": "Prior needs.",
            "stages": [{"stage_name": "Account Configuration"}],
        }
    ]
    generate_l2_capabilities(**_l2_args(examples=examples), llm=llm)
    # The current selected stage names are allowed; historic example stage data is not.
    assert "stage_name" not in llm.last_query
    assert "Prior theme." in llm.last_query
