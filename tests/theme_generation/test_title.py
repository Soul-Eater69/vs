"""Fake-only tests for theme-title generation.

No live Azure / Jira / LLM: a fake LLM returns a structured title and records the
prompt so we can assert no historic stage context leaks in.
"""

from __future__ import annotations

from typing import Any

from vs_app.theme_generation.models import GeneratedL2Capability, GeneratedL3Capability
from vs_app.theme_generation.title import generate_theme_title

VS = "Configure, Price, and Quote"
SELECTED_STAGES = [{"stage": "Account Configuration", "confidence": 0.9, "reason": "set up"}]
L2 = [GeneratedL2Capability("Quote Management", "Manage quotes.", 0.9)]
L3 = [GeneratedL3Capability("Quote Versioning", "Quote Management", "Track versions.", 0.7)]


class FakeLLM:
    def __init__(self, title: str = "Automated Quoting and Account Configuration") -> None:
        self.title = title
        self.last_query = ""

    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        self.last_query = query
        return output_schema(theme_title=self.title)


class MalformedLLM:
    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        raise ValueError("gateway boom")


def _args(**overrides) -> dict:
    args = dict(
        idea_context="quote automation idea",
        value_stream_name=VS,
        selected_stages=SELECTED_STAGES,
        theme_description="A CPQ theme.",
        business_needs="Faster quoting.",
        l2_capabilities=L2,
        l3_capabilities=L3,
    )
    args.update(overrides)
    return args


def test_generates_title() -> None:
    title, warnings = generate_theme_title(**_args(), llm=FakeLLM())
    assert title == "Automated Quoting and Account Configuration"
    assert warnings == []


def test_no_llm_returns_empty_with_warning() -> None:
    title, warnings = generate_theme_title(**_args(), llm=None)
    assert title == ""
    assert any("no llm provided" in w for w in warnings)


def test_malformed_returns_empty_with_warning() -> None:
    title, warnings = generate_theme_title(**_args(), llm=MalformedLLM())
    assert title == ""
    assert any("title generation failed" in w for w in warnings)


def test_empty_title_warns() -> None:
    title, warnings = generate_theme_title(**_args(), llm=FakeLLM(title="   "))
    assert title == ""
    assert any("empty theme_title" in w for w in warnings)


def test_jira_id_in_title_warns() -> None:
    title, warnings = generate_theme_title(**_args(), llm=FakeLLM(title="Quoting for IDMT-1001"))
    assert any("Jira-like IDs" in w for w in warnings)


def test_prompt_has_context_but_no_historic_stage_keys() -> None:
    llm = FakeLLM()
    generate_theme_title(**_args(), llm=llm)
    q = llm.last_query
    # context present
    assert "A CPQ theme." in q
    assert "Quote Management" in q
    assert "Quote Versioning" in q
    # current selected stage name is allowed context (not historic)
    assert "Account Configuration" in q
    # no historic stage-context keys
    assert "stage_name" not in q
    assert "support_type" not in q
