from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService


def _minimal_payload() -> dict:
    return {
        "selected_value_streams": [],
        "auto_selected_value_streams": [],
        "llm_selected_value_streams": [],
        "rescued_confirmed_merged_value_streams": [],
        "rescued_historical_gap_fill_value_streams": [],
        "dropped_historical_gap_fill_value_streams": [],
        "rejected_candidates": [],
        "semantic_candidate_value_streams": [],
        "historical_candidate_value_streams": [],
        "merged_candidate_value_streams": [],
        "historical_ticket_hits": [],
        "historical_value_stream_support": [],
        "candidate_value_streams": [],
        "llm_candidates": [],
        "historical_source": "test",
        "raw_response": None,
        "direct_llm_output": None,
        "historical_llm_output": None,
        "query_preparation": {},
        "warnings": [],
        "evidence": [],
        "debug": {},
    }


def test_source_ticket_exclusion_is_passed_to_pipeline() -> None:
    captured: dict = {}

    def pipeline_fn(query: str, **kwargs) -> dict:
        captured["query"] = query
        captured["exclude_ticket_ids"] = kwargs.get("exclude_ticket_ids")
        payload = _minimal_payload()
        payload["historical_excluded_ticket_ids"] = list(kwargs.get("exclude_ticket_ids") or [])
        return payload

    service = ValueStreamRagService(pipeline_fn=pipeline_fn)
    result = asyncio.run(
        service.analyze(
            ValueStreamRagCommand(
                ticket_id="IDMT-123",
                idea_card_text="uploaded card text",
            )
        )
    )

    assert captured["query"] == "uploaded card text"
    assert captured["exclude_ticket_ids"] == ["IDMT-123"]
    assert result.historical_excluded_ticket_ids == ["IDMT-123"]


def test_source_ticket_exclusion_can_be_disabled() -> None:
    captured: dict = {}

    def pipeline_fn(query: str, **kwargs) -> dict:
        captured["exclude_ticket_ids"] = kwargs.get("exclude_ticket_ids")
        return _minimal_payload()

    service = ValueStreamRagService(pipeline_fn=pipeline_fn)
    asyncio.run(
        service.analyze(
            ValueStreamRagCommand(
                ticket_id="IDMT-123",
                idea_card_text="uploaded card text",
                exclude_source_ticket_from_historical=False,
            )
        )
    )

    assert captured["exclude_ticket_ids"] is None
