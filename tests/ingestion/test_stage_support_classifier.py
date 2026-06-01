"""Tests for the stage support classifier.

The classifier delegates the LLM call to ingestion.summary.llm_io, so the tests
patch ``llm_io.complete_text`` (where the call resolves) to return canned JSON
and assert validation/drop behavior. The real prompt YAML + schema are exercised
because the prompt is built before the (patched) call.
"""

from __future__ import annotations

import json

from vs_app.ingestion.ground_truth.stage_support import classify_stage_support
from vs_app.ingestion.index_documents.idmt_document_builder import (
    build_indexed_idmt_document,
)
from vs_app.ingestion.index_documents.models import TicketContext
from vs_app.ingestion.summary import llm_io

VS = "Manage Utilization Management Program"
GT = {VS: ["Manage UM Operations", "Evaluate UM Performance"]}
CTX = "[DESCRIPTION]\nUM operations workflow for prior authorization."


class Cfg:
    classification_input_char_limit = 20_000


def _item(vs, stage, support, *, reason="because", evidence="quote", confidence=0.9):
    return {
        "value_stream_name": vs,
        "stage_name": stage,
        "support_type": support,
        "reason": reason,
        "evidence": evidence,
        "confidence": confidence,
    }


def _run(monkeypatch, response, *, gt=GT, text=CTX, llm_client=object()):
    monkeypatch.setattr(llm_io, "complete_text", lambda *a, **k: response)
    return classify_stage_support(
        ticket_id="IDMT-1",
        consolidated_text=text,
        gt_by_value_stream=gt,
        llm_client=llm_client,
        cfg=Cfg(),
    )


def test_direct_classification_accepted(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "direct")]}))
    assert len(rows) == 1
    assert rows[0].value_stream_name == VS
    assert rows[0].stage_name == "Manage UM Operations"
    assert rows[0].support_type == "direct"
    assert rows[0].source == "llm_stage_support"
    assert rows[0].confidence == 0.9


def test_implied_classification_accepted(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "implied")]}))
    assert [r.support_type for r in rows] == ["implied"]


def test_weak_broad_is_accepted_and_not_collapsed(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "weak_broad")]}))
    assert len(rows) == 1
    assert rows[0].support_type == "weak_broad"


def test_not_in_context_classification_accepted(monkeypatch) -> None:
    rows = _run(
        monkeypatch,
        json.dumps({"stages": [_item(VS, "Evaluate UM Performance", "not_in_context", evidence="")]}),
    )
    assert len(rows) == 1
    assert rows[0].support_type == "not_in_context"
    assert rows[0].stage_name == "Evaluate UM Performance"


def test_invented_stage_is_dropped(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Totally Invented Stage", "direct")]}))
    assert rows == []


def test_invented_value_stream_is_dropped(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item("Some Other VS", "Manage UM Operations", "direct")]}))
    assert rows == []


def test_invalid_support_type_is_dropped(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "unknown")]}))
    assert rows == []


def test_missing_gt_stage_does_not_error(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "direct")]}))
    assert [r.stage_name for r in rows] == ["Manage UM Operations"]


def test_returns_empty_without_llm_or_context_or_gt(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("LLM must not be called")

    monkeypatch.setattr(llm_io, "complete_text", _boom)
    assert classify_stage_support(
        ticket_id="t", consolidated_text=CTX, gt_by_value_stream=GT, llm_client=None, cfg=Cfg()
    ) == []
    assert classify_stage_support(
        ticket_id="t", consolidated_text="   ", gt_by_value_stream=GT, llm_client=object(), cfg=Cfg()
    ) == []
    assert classify_stage_support(
        ticket_id="t", consolidated_text=CTX, gt_by_value_stream={}, llm_client=object(), cfg=Cfg()
    ) == []


def test_malformed_llm_response_returns_empty(monkeypatch) -> None:
    assert _run(monkeypatch, "not json at all") == []


def test_empty_llm_response_returns_empty(monkeypatch) -> None:
    assert _run(monkeypatch, "") == []


def test_document_builder_backfills_unknown_for_uncovered_stage(monkeypatch) -> None:
    rows = _run(monkeypatch, json.dumps({"stages": [_item(VS, "Manage UM Operations", "direct")]}))

    doc = build_indexed_idmt_document(
        ticket_context=TicketContext(ticket_id="IDMT-1", summary="s"),
        value_stream_support=[],
        gt_by_value_stream=GT,
        stage_support=rows,
    )

    by_stage = {row["stage_name"]: row for row in doc["stage_support"]}
    assert by_stage["Manage UM Operations"]["support_type"] == "direct"
    assert by_stage["Manage UM Operations"]["source"] == "llm_stage_support"
    assert by_stage["Evaluate UM Performance"]["support_type"] == "unknown"
    assert by_stage["Evaluate UM Performance"]["source"] == "jira_gt"
