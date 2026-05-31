from __future__ import annotations

import json

from vs_app.ingestion.index_documents.idmt_document_builder import (
    RETRIEVAL_TEXT_MAX_CHARS,
    build_document_from_stage_dataset_row,
    build_indexed_idmt_document,
)
from vs_app.ingestion.index_documents.models import (
    StageSupport,
    TicketContext,
    ValueStreamSupport,
)


def _sample_row() -> dict:
    return {
        "idea_card": {
            "summary": "Add quoting support",
            "description": "Need quote available for sales executive",
            "generated_summary": "Quoting support and account setup",
        },
        "ground_truth": {
            "gt_by_value_stream": {
                "Configure, Price, and Quote": [
                    "Generate Quote and Present to Customer",
                    "Account Configuration",
                ]
            }
        },
        "warnings": ["sample warning"],
    }


def test_converts_current_stage_dataset_row() -> None:
    doc = build_document_from_stage_dataset_row("IDMT-1", _sample_row())

    assert doc["ticket_id"] == "IDMT-1"
    assert doc["id"] == "IDMT-1"
    assert doc["ticket_key"] == "IDMT-1"
    assert "Configure, Price, and Quote" in doc["valid_value_stream_names"]
    assert "Generate Quote and Present to Customer" in doc["stage_names"]
    assert "Account Configuration" in doc["stage_names"]
    assert "Configure, Price, and Quote::Account Configuration" in doc["stage_pairs"]
    assert (
        "Configure, Price, and Quote::Generate Quote and Present to Customer"
        in doc["stage_pairs"]
    )
    assert doc["has_stage_gt"] is True
    assert doc["stage_count"] == 2
    assert doc["has_valid_value_streams"] is True
    assert doc["warnings"] == ["sample warning"]
    # Defaults applied without regenerating summaries.
    assert doc["generated_summary"] == "Quoting support and account setup"
    assert doc["source_system"] == "jira"
    assert doc["index_version"] == "idmt-v2-stage-history"
    assert doc["ingested_at"]


def test_gt_by_value_stream_json_is_deterministic() -> None:
    doc = build_document_from_stage_dataset_row("IDMT-1", _sample_row())

    assert json.loads(doc["gt_by_value_stream_json"]) == {
        "Configure, Price, and Quote": [
            "Account Configuration",
            "Generate Quote and Present to Customer",
        ]
    }


def test_missing_stage_support_creates_unknown_fallback_rows() -> None:
    doc = build_document_from_stage_dataset_row("IDMT-1", _sample_row())

    assert doc["stage_support"]
    assert all(row["support_type"] == "unknown" for row in doc["stage_support"])
    assert all(row["source"] == "jira_gt" for row in doc["stage_support"])
    assert {row["stage_name"] for row in doc["stage_support"]} == {
        "Account Configuration",
        "Generate Quote and Present to Customer",
    }


def test_empty_ground_truth_has_no_stages() -> None:
    doc = build_document_from_stage_dataset_row(
        "IDMT-2",
        {"idea_card": {"summary": "No GT yet"}, "ground_truth": {}, "warnings": []},
    )

    assert doc["has_stage_gt"] is False
    assert doc["stage_count"] == 0
    assert doc["stage_names"] == []
    assert doc["stage_pairs"] == []
    assert doc["stage_support"] == []


def test_duplicate_and_messy_values_are_cleaned_and_deduped() -> None:
    ticket_context = TicketContext(
        ticket_id="  idmt-3  ",
        summary="  spaced   summary  ",
    )
    value_stream_support = [
        ValueStreamSupport(value_stream_name="  Configure, Price, and Quote  ", source="jira_gt"),
        ValueStreamSupport(value_stream_name="Configure, Price, and Quote", source="jira_gt"),
        ValueStreamSupport(value_stream_name="   ", source="jira_gt"),
    ]
    gt_by_value_stream = {
        "Configure, Price, and Quote": [
            "Account Configuration",
            "  Account Configuration  ",
            "",
        ],
        "  Configure, Price, and Quote  ": ["Generate Quote and Present to Customer"],
    }

    doc = build_indexed_idmt_document(
        ticket_context=ticket_context,
        value_stream_support=value_stream_support,
        gt_by_value_stream=gt_by_value_stream,
    )

    assert doc["ticket_id"] == "IDMT-3"
    assert doc["summary"] == "spaced summary"
    assert doc["valid_value_stream_names"] == ["Configure, Price, and Quote"]
    assert doc["stage_names"] == [
        "Account Configuration",
        "Generate Quote and Present to Customer",
    ]
    assert doc["value_stream_support"] and len(doc["value_stream_support"]) == 1


def test_partial_stage_support_is_merged_with_gt_fallback() -> None:
    ticket_context = TicketContext(ticket_id="IDMT-4", summary="Quoting work")
    stage_support = [
        StageSupport(
            value_stream_name="Configure, Price, and Quote",
            stage_name="Account Configuration",
            support_type="direct",
            source="description",
            confidence=0.9,
        )
    ]
    gt_by_value_stream = {
        "Configure, Price, and Quote": [
            "Account Configuration",
            "Generate Quote and Present to Customer",
        ]
    }

    doc = build_indexed_idmt_document(
        ticket_context=ticket_context,
        value_stream_support=[],
        gt_by_value_stream=gt_by_value_stream,
        stage_support=stage_support,
    )

    rows = {row["stage_name"]: row for row in doc["stage_support"]}
    assert set(rows) == {"Account Configuration", "Generate Quote and Present to Customer"}
    # Provided classification is preserved.
    assert rows["Account Configuration"]["support_type"] == "direct"
    assert rows["Account Configuration"]["source"] == "description"
    # The uncovered GT stage gets an unknown fallback row.
    assert rows["Generate Quote and Present to Customer"]["support_type"] == "unknown"
    assert rows["Generate Quote and Present to Customer"]["source"] == "jira_gt"


def test_document_text_preserves_line_breaks() -> None:
    ticket_context = TicketContext(
        ticket_id="IDMT-5",
        summary="Line one\nLine two",
        description="Para one\n\n\n\nPara two   with   spaces",
    )

    doc = build_indexed_idmt_document(
        ticket_context=ticket_context,
        value_stream_support=[],
        gt_by_value_stream={},
    )

    # Summary is a label field: whitespace collapsed.
    assert doc["summary"] == "Line one Line two"
    # Description is a document field: line breaks preserved, blank runs squeezed.
    assert doc["description"] == "Para one\n\nPara two with spaces"


def test_fallback_retrieval_text_is_capped() -> None:
    ticket_context = TicketContext(
        ticket_id="IDMT-6",
        summary="x" * 6000,
    )

    doc = build_indexed_idmt_document(
        ticket_context=ticket_context,
        value_stream_support=[],
        gt_by_value_stream={},
    )

    assert len(doc["retrieval_text"]) <= RETRIEVAL_TEXT_MAX_CHARS
