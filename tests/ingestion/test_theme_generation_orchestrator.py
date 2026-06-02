"""Tests for Theme-generation orchestration (Feature 14B2; fakes only)."""

from __future__ import annotations

from typing import Any

import vs_app.ingestion.theme_generation.orchestrator as orch
from vs_app.ingestion.theme_generation.orchestrator import (
    generate_theme_for_value_stream,
    generate_themes_for_idea,
)

VS = "Configure, Price, and Quote"
ALLOWED = ["Account Configuration", "Generate Quote and Present to Customer"]

EXAMPLES = [
    {
        "ticket_id": "IDMT-1",
        "group_id": "GROUP-1",
        "theme_description": "Prior CPQ theme.",
        "business_needs": "Faster quoting.",
        "value_streams": [{"value_stream_name": VS}],
        "stages": [{"stage_name": "Account Configuration"}],
        "content_vector": [0.1, 0.2],
        "content": "RAW",
    }
]

CATALOG = {
    VS: {
        "value_stream_id": "VS-CPQ",
        "stages": [
            {"name": "Account Configuration"},
            {"name": "Generate Quote and Present to Customer"},
        ],
    }
}


def _patch_stage_selector(monkeypatch, predicted, warnings=None):
    def fake(*, idea_card_text, value_stream_name, allowed_stages, llm):
        return {
            "value_stream_name": value_stream_name,
            "allowed_stages": allowed_stages,
            "predicted_stages": predicted,
            "warnings": warnings or [],
            "raw_response": "",
        }

    monkeypatch.setattr(orch, "predict_value_stream_stages", fake)


def _patch_theme_generator(monkeypatch, *, theme="A CPQ theme.", needs="Quoting.", warnings=None):
    def fake(*, idea_context, value_stream_name, allowed_stages, selected_stages, examples, llm):
        return {
            "theme_description": theme,
            "business_needs": needs,
            "warnings": warnings or [],
            "raw_response": "",
        }

    monkeypatch.setattr(orch, "generate_theme_description", fake)


# --- generate_theme_for_value_stream ----------------------------------------


def test_combines_stage_selector_and_theme_generator(monkeypatch) -> None:
    _patch_stage_selector(
        monkeypatch, [{"stage": "Account Configuration", "confidence": 0.9, "reason": "setup"}]
    )
    _patch_theme_generator(monkeypatch)
    out = generate_theme_for_value_stream(
        idea_context="idea", value_stream_name=VS, allowed_stages=ALLOWED, examples=EXAMPLES, llm=object()
    )
    assert out["value_stream_name"] == VS
    assert out["theme_description"] == "A CPQ theme."
    assert out["business_needs"] == "Quoting."
    assert out["selected_stages"] == [
        {"stage": "Account Configuration", "confidence": 0.9, "reason": "setup"}
    ]


def test_invented_stage_is_dropped_defensively(monkeypatch) -> None:
    _patch_stage_selector(
        monkeypatch,
        [
            {"stage": "Account Configuration", "confidence": 0.9, "reason": "ok"},
            {"stage": "Totally Invented Stage", "confidence": 0.8, "reason": "bad"},
        ],
    )
    _patch_theme_generator(monkeypatch)
    out = generate_theme_for_value_stream(
        idea_context="idea", value_stream_name=VS, allowed_stages=ALLOWED, examples=[], llm=object()
    )
    assert [s["stage"] for s in out["selected_stages"]] == ["Account Configuration"]
    assert any("dropped non-allowed stage: Totally Invented Stage" in w for w in out["warnings"])


def test_no_examples_still_works(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    out = generate_theme_for_value_stream(
        idea_context="idea", value_stream_name=VS, allowed_stages=ALLOWED, examples=[], llm=object()
    )
    assert out["theme_description"] == "A CPQ theme."
    assert out["examples_used"] == []


def test_warnings_merged_and_deduped(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [], warnings=["shared warning", "stage warning"])
    _patch_theme_generator(monkeypatch, warnings=["shared warning", "theme warning"])
    out = generate_theme_for_value_stream(
        idea_context="idea", value_stream_name=VS, allowed_stages=ALLOWED, examples=[], llm=object()
    )
    assert out["warnings"].count("shared warning") == 1
    assert "stage warning" in out["warnings"]
    assert "theme warning" in out["warnings"]


def test_examples_used_is_ids_only(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    out = generate_theme_for_value_stream(
        idea_context="idea", value_stream_name=VS, allowed_stages=ALLOWED, examples=EXAMPLES, llm=object()
    )
    assert out["examples_used"] == [
        {"ticket_id": "IDMT-1", "group_id": "GROUP-1", "value_stream_name": VS}
    ]
    used = out["examples_used"][0]
    for forbidden in ("theme_description", "business_needs", "content", "content_vector", "stages"):
        assert forbidden not in used


# --- generate_themes_for_idea -----------------------------------------------


class FakeSearchClient:
    def __init__(self, idmt_docs=None, theme_docs=None, raise_on_search=False) -> None:
        self.idmt_docs = idmt_docs or []
        self.theme_docs = theme_docs or {}
        self.raise_on_search = raise_on_search
        self.vector_calls = 0

    def vector_search(self, *, query_vector, top_k, filter_expression, index_name=None):
        self.vector_calls += 1
        if self.raise_on_search:
            raise RuntimeError("search exploded")
        return list(self.idmt_docs)

    def get_document(self, *, doc_id, index_name=None):
        return self.theme_docs.get(doc_id)


def test_one_result_per_selected_vs_in_order(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    out = generate_themes_for_idea(
        idea_context="idea",
        selected_value_streams=[VS, "Manage Leads and opportunities"],
        stage_catalog=CATALOG,
        llm=object(),
    )
    assert [r["value_stream_name"] for r in out] == [VS, "Manage Leads and opportunities"]


def test_high_level_retrieval_path(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    idmt = {
        "document_type": "idmt",
        "ticket_id": "IDMT-1",
        "@search.score": 1.0,
        "properties": {"value_streams": [{"group_id": "GROUP-1", "value_stream_name": VS}]},
    }
    theme = {
        "id": "theme::IDMT-1::GROUP-1",
        "ticket_id": "IDMT-1",
        "group_id": "GROUP-1",
        "properties": {
            "theme_description": "td",
            "business_needs": "bn",
            "value_streams": [{"value_stream_name": VS}],
            "stages": [],
        },
    }
    client = FakeSearchClient(idmt_docs=[idmt], theme_docs={"theme::IDMT-1::GROUP-1": theme})
    out = generate_themes_for_idea(
        idea_context="idea",
        selected_value_streams=[VS],
        stage_catalog=CATALOG,
        llm=object(),
        search_client=client,
        query_vector=[0.1, 0.2],
    )
    assert client.vector_calls == 1
    assert out[0]["examples_used"] == [
        {"ticket_id": "IDMT-1", "group_id": "GROUP-1", "value_stream_name": VS}
    ]


def test_retrieval_failure_degrades_to_empty_examples(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    client = FakeSearchClient(raise_on_search=True)
    out = generate_themes_for_idea(
        idea_context="idea",
        selected_value_streams=[VS],
        stage_catalog=CATALOG,
        llm=object(),
        search_client=client,
        query_vector=[0.1],
    )
    assert out[0]["examples_used"] == []
    assert any("theme example retrieval failed" in w for w in out[0]["warnings"])


def test_no_client_or_vector_skips_retrieval(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    # no search_client / query_vector -> still generates, no retrieval
    out = generate_themes_for_idea(
        idea_context="idea", selected_value_streams=[VS], stage_catalog=CATALOG, llm=object()
    )
    assert out[0]["theme_description"] == "A CPQ theme."
    assert out[0]["examples_used"] == []


def test_blank_value_stream_is_skipped(monkeypatch) -> None:
    _patch_stage_selector(monkeypatch, [])
    _patch_theme_generator(monkeypatch)
    out = generate_themes_for_idea(
        idea_context="idea",
        selected_value_streams=["", "   ", VS],
        stage_catalog=CATALOG,
        llm=object(),
    )
    assert [r["value_stream_name"] for r in out] == [VS]


def test_allowed_stages_loaded_from_catalog(monkeypatch) -> None:
    captured = {}

    def fake_stage(*, idea_card_text, value_stream_name, allowed_stages, llm):
        captured["allowed_stages"] = allowed_stages
        return {"predicted_stages": [], "warnings": [], "raw_response": ""}

    monkeypatch.setattr(orch, "predict_value_stream_stages", fake_stage)
    _patch_theme_generator(monkeypatch)
    generate_themes_for_idea(
        idea_context="idea", selected_value_streams=[VS], stage_catalog=CATALOG, llm=object()
    )
    assert captured["allowed_stages"] == [
        "Account Configuration",
        "Generate Quote and Present to Customer",
    ]


def test_no_forbidden_imports() -> None:
    import ast

    tree = ast.parse(open(orch.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [a.name for a in node.names]
    blob = " ".join(imported).lower()
    for forbidden in ("azure", "jira", "embed", "aisearch", " idpchat"):
        assert forbidden not in blob, f"unexpected import referencing {forbidden}"
