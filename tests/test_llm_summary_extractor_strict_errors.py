from __future__ import annotations

import pytest

from vs_app.ingestion.summary import llm_summary_extractor as extractor
from vs_app.ingestion.summary.llm_summary_extractor import (
    SummaryExtractionError,
    ValueStreamClassificationError,
    classify_ticket_value_streams,
    summarize_ticket,
)


class Cfg:
    llm_model = "test-model"
    llm_max_output_tokens = 1200
    summary_input_char_limit = 20_000
    classification_input_char_limit = 20_000
    strict_value_stream_classification = True


def test_empty_consolidated_text_raises() -> None:
    with pytest.raises(SummaryExtractionError, match="Consolidated text is empty"):
        summarize_ticket("IDMT-1", "", object(), Cfg())


def test_llm_empty_response_raises(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "complete_text", lambda *args, **kwargs: "")

    with pytest.raises(SummaryExtractionError, match="LLM returned empty response"):
        summarize_ticket("IDMT-1", "real source text", object(), Cfg())


def test_invalid_summary_json_raises(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "complete_text", lambda *args, **kwargs: "not json")

    with pytest.raises(SummaryExtractionError, match="Missing summary_text"):
        summarize_ticket("IDMT-1", "real source text", object(), Cfg())


def test_labeled_ticket_missing_classification_rows_raises(monkeypatch) -> None:
    monkeypatch.setattr(extractor, "complete_text", lambda *args, **kwargs: '{"value_streams": []}')

    with pytest.raises(ValueStreamClassificationError, match="returned no rows"):
        classify_ticket_value_streams(
            ticket_id="IDMT-1",
            consolidated_text="real source text",
            value_stream_ids=["GROUP-1"],
            value_stream_names=["Manage Member Care"],
            label_source="jira_implemented_by_group_links",
            llm_client=object(),
            cfg=Cfg(),
        )


def test_labeled_ticket_missing_coverage_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        extractor,
        "complete_text",
        lambda *args, **kwargs: (
            '{"value_streams": [{"vs_name": "Other", "inference_type": "direct", "reason": "x"}]}'
        ),
    )

    with pytest.raises(ValueStreamClassificationError, match="missed labels"):
        classify_ticket_value_streams(
            ticket_id="IDMT-1",
            consolidated_text="real source text",
            value_stream_ids=["GROUP-1"],
            value_stream_names=["Manage Member Care"],
            label_source="jira_implemented_by_group_links",
            llm_client=object(),
            cfg=Cfg(),
        )


def test_summary_input_char_limit_comes_from_cfg(monkeypatch) -> None:
    captured = {}

    class SmallCfg(Cfg):
        summary_input_char_limit = 1000

    def fake_complete_text(prompt, *args, **kwargs):
        captured["prompt"] = prompt
        return (
            '{"summary_text":"summary","business_problem":"problem",'
            '"business_capability":"capability","stakeholders":[],'
            '"systems_and_products":[],"key_terms":[]}'
        )

    monkeypatch.setattr(extractor, "complete_text", fake_complete_text)

    summarize_ticket("IDMT-1", "x" * 1500, object(), SmallCfg())

    assert "x" * 1000 in captured["prompt"]
    assert "x" * 1001 not in captured["prompt"]
