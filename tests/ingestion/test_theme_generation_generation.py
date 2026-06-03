"""Tests for pure theme text generation (Feature 14B1; fake LLM only)."""

from __future__ import annotations

import json
from typing import Any

from vs_app.theme_generation.descriptions import generate_theme_description
from vs_app.modules.prompts.loader import build_theme_generation_prompt
from vs_app.modules.prompts.schemas import ThemeGenerationResult

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
    """Mirrors the generate_structured(...) duck-type used by the stage selector."""

    def __init__(self, theme_description="A CPQ theme.", business_needs="Faster quoting.") -> None:
        self.result = ThemeGenerationResult(
            theme_description=theme_description, business_needs=business_needs
        )
        self.calls: list[dict] = []

    def generate_structured(self, *, query, output_schema, system_prompt, reasoning_effort):
        self.calls.append({"query": query, "system_prompt": system_prompt})
        return self.result


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


# --- prompt assembly --------------------------------------------------------


def test_prompt_includes_all_sections() -> None:
    prompt = build_theme_generation_prompt(
        idea_context="MY IDEA CONTEXT",
        value_stream_name=VS,
        allowed_stages=json.dumps(ALLOWED),
        selected_stages=json.dumps(["Account Configuration"]),
        examples=json.dumps(EXAMPLES),
    )
    assert "MY IDEA CONTEXT" in prompt
    assert VS in prompt
    assert "Account Configuration" in prompt
    assert "Generate Quote and Present to Customer" in prompt
    assert "Prior CPQ theme." in prompt


def test_prompt_excludes_content_vector_and_raw_content() -> None:
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
    # but the safe example fields are still present
    assert "Prior CPQ theme." in sent


# --- generation behavior ----------------------------------------------------


def test_successful_generation_returns_fields() -> None:
    out = _gen(FakeStructuredLLM("A clear CPQ theme.", "Quoting needs."))
    assert out["theme_description"] == "A clear CPQ theme."
    assert out["business_needs"] == "Quoting needs."
    assert "empty theme_description" not in out["warnings"]


def test_text_llm_with_valid_json_is_parsed() -> None:
    payload = json.dumps({"theme_description": "From text LLM.", "business_needs": "Needs."})
    out = _gen(FakeTextLLM(payload))
    assert out["theme_description"] == "From text LLM."
    assert out["business_needs"] == "Needs."


def test_no_examples_still_works() -> None:
    out = _gen(FakeStructuredLLM(), examples=[])
    assert out["theme_description"]
    assert "no historic examples available" in out["warnings"]


def test_llm_none_returns_blank_with_warning() -> None:
    out = _gen(None)
    assert out["theme_description"] == ""
    assert out["business_needs"] == ""
    assert "no llm provided for theme generation" in out["warnings"]


def test_invalid_json_returns_blank_with_warning_no_raise() -> None:
    out = _gen(FakeTextLLM("this is not json at all"))
    assert out["theme_description"] == ""
    assert out["business_needs"] == ""
    assert any("invalid" in w or "empty" in w for w in out["warnings"])


def test_empty_theme_description_warns() -> None:
    out = _gen(FakeStructuredLLM(theme_description="", business_needs="needs only"))
    assert out["theme_description"] == ""
    assert "empty theme_description" in out["warnings"]


def test_jira_id_leakage_adds_warning() -> None:
    out = _gen(FakeStructuredLLM(theme_description="Theme references IDMT-1001 and GROUP-2001."))
    assert "generated text contains Jira-like IDs" in out["warnings"]


def test_empty_allowed_and_selected_warn_but_still_generate() -> None:
    out = _gen(FakeStructuredLLM(), allowed_stages=[], selected_stages=[])
    assert out["theme_description"]  # still generated
    assert "no allowed stages for value stream" in out["warnings"]
    assert "no selected stages provided" in out["warnings"]


def test_loader_and_schema_exports_work() -> None:
    # schema is exported and lenient
    assert ThemeGenerationResult().theme_description == ""
    # loader function is importable and renders
    prompt = build_theme_generation_prompt(
        idea_context="x",
        value_stream_name="y",
        allowed_stages="[]",
        selected_stages="[]",
        examples="[]",
    )
    assert "JSON only" in prompt


def test_no_retrieval_or_stage_prediction_imports() -> None:
    # The generation module must not IMPORT retrieval / stage-prediction / azure /
    # jira / embedding code (docstrings may reference them by name; imports may not).
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
