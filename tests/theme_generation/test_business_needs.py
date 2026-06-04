"""Fake-only tests for Business Needs generation."""

from __future__ import annotations

from vs_app.modules.prompts.schemas import BusinessNeedsResult
from vs_app.theme_generation.business_needs import generate_business_needs

VS = "Configure, Price, and Quote"
SELECTED = [
    {"stage": "Account Configuration", "confidence": 0.9, "reason": "setup"},
    {"stage": "Generate Quote and Present to Customer", "confidence": 0.8, "reason": "quote"},
]


class FakeStructuredLLM:
    def __init__(self, business_needs="Stage-by-stage needs.") -> None:
        self.business_needs = business_needs
        self.calls: list[dict] = []

    def generate_structured(self, *, query, output_schema, system_prompt, reasoning_effort):
        self.calls.append({"query": query, "system_prompt": system_prompt})
        return BusinessNeedsResult(business_needs=self.business_needs)


class MalformedLLM:
    def generate_structured(self, *, query, output_schema, system_prompt, reasoning_effort):
        raise ValueError("gateway boom")


def _gen(llm, **overrides):
    kwargs = dict(
        idea_context="Sales need faster account setup and quoting.",
        value_stream_name=VS,
        selected_stages=SELECTED,
        llm=llm,
    )
    kwargs.update(overrides)
    return generate_business_needs(**kwargs)


def test_generates_business_needs() -> None:
    out = _gen(FakeStructuredLLM("Quoting operational needs."))
    assert out["business_needs"] == "Quoting operational needs."
    assert "theme_description" not in out


def test_prompt_organized_by_selected_stages() -> None:
    fake = FakeStructuredLLM()
    _gen(fake)
    sent = fake.calls[0]["query"]
    assert "Account Configuration" in sent
    assert "Generate Quote and Present to Customer" in sent
    assert "organized by" in sent.lower()


def test_prompt_says_ignore_assumptions() -> None:
    fake = FakeStructuredLLM()
    _gen(fake)
    sent = fake.calls[0]["query"]
    assert "assumption" in sent.lower()


def test_prompt_has_no_l2_l3_or_historic_context() -> None:
    fake = FakeStructuredLLM()
    _gen(fake)
    sent = fake.calls[0]["query"]
    # business needs never receives capabilities or historic examples
    assert "l2_capabilities" not in sent.lower()
    assert "l3_capabilities" not in sent.lower()
    assert "historic" not in sent.lower() or "historic stage context" in sent.lower()


def test_no_llm_returns_empty_with_warning() -> None:
    out = _gen(None)
    assert out["business_needs"] == ""
    assert any("no llm provided" in w for w in out["warnings"])


def test_malformed_returns_empty_with_warning() -> None:
    out = _gen(MalformedLLM())
    assert out["business_needs"] == ""
    assert any("business needs failed" in w for w in out["warnings"])


def test_no_selected_stages_warns() -> None:
    out = _gen(FakeStructuredLLM(), selected_stages=[])
    assert "no selected stages provided" in out["warnings"]


def test_jira_id_leakage_warns() -> None:
    out = _gen(FakeStructuredLLM(business_needs="Needs reference EPIC-10 work."))
    assert any("Jira-like IDs" in w for w in out["warnings"])


def test_signature_does_not_accept_capabilities() -> None:
    import pytest

    for bad in ("l2_capabilities", "l3_capabilities", "examples"):
        with pytest.raises(TypeError):
            generate_business_needs(
                idea_context="i", value_stream_name=VS, selected_stages=SELECTED, llm=None, **{bad: "x"}
            )


def test_schema_export() -> None:
    assert BusinessNeedsResult().business_needs == ""
