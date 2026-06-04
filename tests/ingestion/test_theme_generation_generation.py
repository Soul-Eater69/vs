"""Tests for Theme Description generation (split from Business Needs; fake LLM)."""

from __future__ import annotations

import json
from typing import Any

from vs_app.modules.prompts.schemas import ThemeDescriptionResult, ThemeGenerationResult
from vs_app.theme_generation.descriptions import generate_theme_description

VS = "Configure, Price, and Quote"
ALLOWED = ["Account Configuration", "Generate Quote and Present to Customer"]
SELECTED = [{"stage": "Account Configuration", "confidence": 0.9, "reason": "setup"}]
EXAMPLES = [
    {
        "ticket_id": "IDMT-1",
        "group_id": "GROUP-1",
        "theme_description": "Prior CPQ theme.",
        "business_needs": "Faster quoting.",
        "value_streams": [{"value_stream_name": VS}],
        "stages": [{"stage_name": "Account Configuration"}],
    }
]


class FakeStructuredLLM:
    """generate_structured(...) duck-type returning a theme description."""

    def __init__(self, theme_description="A CPQ theme.") -> None:
        self.theme_description = theme_description
        self.calls: list[dict] = []

    def generate_structured(self, *, query, output_schema, system_prompt, reasoning_effort):
        self.calls.append({"query": query, "system_prompt": system_prompt})
        return ThemeDescriptionResult(theme_description=self.theme_description)


class FakeTextLLM:
    """An invoke()-style client returning raw text content."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[Any] = []

    def invoke(self, messages):
        self.calls.append(messages)
        return type("Reply", (), {"content": self.content})()


def _gen(llm, **overrides):
    kwargs = dict(
        idea_context="Sales need faster account setup and quoting.",
        value_stream_name=VS,
        allowed_stages=ALLOWED,
        selected_stages=SELECTED,
        examples=EXAMPLES,
        llm=llm,
    )
    kwargs.update(overrides)
    return generate_theme_description(**kwargs)


# --- prompt content --------------------------------------------------------


def test_description_prompt_has_theme_description_and_product_availability() -> None:
    fake = FakeStructuredLLM()
    _gen(fake)
    sent = fake.calls[0]["query"]
    assert "Theme Description" in sent
    assert "Product Availability" in sent
    assert "MY IDEA CONTEXT".lower() not in sent  # sanity
    assert "Sales need faster account setup and quoting." in sent


def test_description_prompt_forbids_fabrication_assumptions_and_historic_stages() -> None:
    fake = FakeStructuredLLM()
    _gen(fake)
    sent = fake.calls[0]["query"]
    assert "fabricate" in sent.lower()
    assert "assumption" in sent.lower()
    assert "historic stage context" in sent.lower()


def test_prompt_excludes_content_vector_raw_content_and_historic_stages() -> None:
    noisy = [
        {
            **EXAMPLES[0],
            "content_vector": [0.1, 0.2, 0.3],
            "content": "RAW_BLOB_" + "x" * 5000,
        }
    ]
    fake = FakeStructuredLLM()
    _gen(fake, examples=noisy)
    sent = fake.calls[0]["query"]
    assert "content_vector" not in sent
    assert "RAW_BLOB_" not in sent
    # historic example stages are never included
    assert "stage_name" not in sent
    # safe style field still present
    assert "Prior CPQ theme." in sent


# --- generation behavior ----------------------------------------------------


def test_successful_generation_returns_description() -> None:
    out = _gen(FakeStructuredLLM("A clear CPQ theme."))
    assert out["theme_description"] == "A clear CPQ theme."
    assert "business_needs" not in out
    assert "empty theme_description" not in out["warnings"]


def test_text_llm_with_valid_json_is_parsed() -> None:
    out = _gen(FakeTextLLM(json.dumps({"theme_description": "From text LLM."})))
    assert out["theme_description"] == "From text LLM."


def test_no_examples_still_works() -> None:
    out = _gen(FakeStructuredLLM(), examples=[])
    assert out["theme_description"]
    assert "no historic examples available" in out["warnings"]


def test_llm_none_returns_blank_with_warning() -> None:
    out = _gen(None)
    assert out["theme_description"] == ""
    assert "no llm provided for theme description" in out["warnings"]


def test_invalid_json_returns_blank_with_warning_no_raise() -> None:
    out = _gen(FakeTextLLM("this is not json at all"))
    assert out["theme_description"] == ""
    assert any("empty" in w or "failed" in w for w in out["warnings"])


def test_empty_theme_description_warns() -> None:
    out = _gen(FakeStructuredLLM(theme_description=""))
    assert out["theme_description"] == ""
    assert "empty theme_description" in out["warnings"]


def test_jira_id_leakage_adds_warning() -> None:
    out = _gen(FakeStructuredLLM(theme_description="Theme references IDMT-1001 and GROUP-2001."))
    assert "generated text contains Jira-like IDs" in out["warnings"]


def test_schema_exports_work() -> None:
    assert ThemeDescriptionResult().theme_description == ""
    # legacy combined schema retained for backward compatibility
    assert ThemeGenerationResult().business_needs == ""


def test_no_retrieval_or_stage_prediction_imports() -> None:
    # The description module must not IMPORT retrieval / stage-prediction / azure /
    # jira / embedding code.
    import ast

    import vs_app.theme_generation.descriptions as gen

    tree = ast.parse(open(gen.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [alias.name for alias in node.names]

    blob = " ".join(imported).lower()
    for forbidden in ("predict_value_stream_stages", "retrieval", "stage_selector", "embed", "azure", "jira"):
        assert forbidden not in blob, f"unexpected import referencing {forbidden}"
