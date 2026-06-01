"""Tests for optional stage-support classification in the dataset builder.

The classifier is patched (no real LLM). These cover the flag gating, row
persistence shape, lenient failure handling, and the document-builder bridge
consuming a persisted ``stage_support`` block.
"""

from __future__ import annotations

import asyncio

import scripts.build_stage_prediction_dataset as dataset
from vs_app.ingestion.index_documents.idmt_document_builder import (
    build_document_from_stage_dataset_row,
)
from vs_app.ingestion.index_documents.models import StageSupport

VS = "Manage Utilization Management Program"
GT = {"gt_by_value_stream": {VS: ["Manage UM Operations", "Evaluate UM Performance"]}}
IDEA = {
    "summary": "summary",
    "description": "description",
    "idea_card_text": "",
    "attachment_text": "",
    "extracted_text": "UM operations workflow context",
    "generated_summary": "gen",
}


def _classify_for_row(monkeypatch, *, llm_client, ground_truth=GT, idea_card=IDEA):
    warnings: list[str] = []
    rows = asyncio.run(
        dataset.classify_stage_support_for_row(
            ticket_id="IDMT-1",
            idea_card=idea_card,
            ground_truth=ground_truth,
            llm_client=llm_client,
            cfg=object(),
            warnings=warnings,
        )
    )
    return rows, warnings


def test_helper_off_returns_empty_and_skips_llm(monkeypatch) -> None:
    def _boom(**_k):
        raise AssertionError("classifier must not run when llm_client is None")

    monkeypatch.setattr(dataset, "classify_stage_support", _boom)
    rows, _ = _classify_for_row(monkeypatch, llm_client=None)
    assert rows == []


def test_helper_on_returns_serialized_rows(monkeypatch) -> None:
    def _fake(*, ticket_id, consolidated_text, gt_by_value_stream, llm_client, cfg):
        return [
            StageSupport(
                value_stream_name=VS,
                stage_name="Manage UM Operations",
                support_type="direct",
                reason="r",
                evidence="e",
                source="llm_stage_support",
                confidence=0.9,
            )
        ]

    monkeypatch.setattr(dataset, "classify_stage_support", _fake)
    rows, _ = _classify_for_row(monkeypatch, llm_client=object())
    assert len(rows) == 1
    assert isinstance(rows[0], dict)  # JSON-ready
    assert rows[0]["support_type"] == "direct"
    assert rows[0]["source"] == "llm_stage_support"
    assert rows[0]["stage_name"] == "Manage UM Operations"


def test_helper_classifier_failure_does_not_raise(monkeypatch) -> None:
    def _raise(**_k):
        raise RuntimeError("llm gateway down")

    monkeypatch.setattr(dataset, "classify_stage_support", _raise)
    rows, warnings = _classify_for_row(monkeypatch, llm_client=object())
    assert rows == []
    assert any("stage support classification failed" in w for w in warnings)


def test_helper_empty_gt_skips_llm(monkeypatch) -> None:
    def _boom(**_k):
        raise AssertionError("classifier must not run when there is no GT")

    monkeypatch.setattr(dataset, "classify_stage_support", _boom)
    rows, _ = _classify_for_row(
        monkeypatch, llm_client=object(), ground_truth={"gt_by_value_stream": {}}
    )
    assert rows == []


def test_build_dataset_ticket_omits_stage_support_when_off() -> None:
    row = asyncio.run(
        dataset.build_dataset_ticket(
            ticket_id="IDMT-1",
            fallback_gt_payload={},
            stage_catalog={},
            jira_client=None,
            classify_support=False,
        )
    )
    assert "stage_support" not in row


def test_build_dataset_ticket_includes_stage_support_key_when_on() -> None:
    row = asyncio.run(
        dataset.build_dataset_ticket(
            ticket_id="IDMT-1",
            fallback_gt_payload={},
            stage_catalog={},
            jira_client=None,
            classify_support=True,
            support_llm_client=object(),
            support_cfg=object(),
        )
    )
    # Key present when enabled; empty here because there is no context/GT to classify.
    assert row["stage_support"] == []


def test_bridge_consumes_persisted_stage_support() -> None:
    row = {
        "idea_card": {"summary": "s", "extracted_text": "ctx"},
        "ground_truth": {"gt_by_value_stream": {VS: ["Manage UM Operations", "Evaluate UM Performance"]}},
        "stage_support": [
            {
                "value_stream_name": VS,
                "stage_name": "Manage UM Operations",
                "support_type": "direct",
                "reason": "r",
                "evidence": "e",
                "source": "llm_stage_support",
                "confidence": 0.9,
            }
        ],
    }

    doc = build_document_from_stage_dataset_row("IDMT-1", row)

    by_stage = {r["stage_name"]: r for r in doc["stage_support"]}
    assert by_stage["Manage UM Operations"]["support_type"] == "direct"
    assert by_stage["Manage UM Operations"]["source"] == "llm_stage_support"
    assert by_stage["Evaluate UM Performance"]["support_type"] == "unknown"
    assert by_stage["Evaluate UM Performance"]["source"] == "jira_gt"


def test_bridge_absent_stage_support_falls_back_to_unknown() -> None:
    row = {
        "idea_card": {"summary": "s"},
        "ground_truth": {"gt_by_value_stream": {VS: ["Manage UM Operations"]}},
    }

    doc = build_document_from_stage_dataset_row("IDMT-1", row)

    assert doc["stage_support"]
    assert all(
        r["support_type"] == "unknown" and r["source"] == "jira_gt"
        for r in doc["stage_support"]
    )
