from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.modules.rag.retrieval.historical_retriever import filter_historical_result


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


def test_filter_historical_result_removes_source_hit_and_rebuilds_support() -> None:
    result = {
        "historical_ticket_hits": [
            {
                "ticket_id": "IDMT-19761",
                "best_score": 0.9,
                "summary_preview": "self match",
                "direct_vs_names": ["Self Stream"],
            },
            {
                "ticket_id": "IDMT-12167",
                "best_score": 0.7,
                "summary_preview": "neighbor",
                "direct_vs_names": ["Neighbor Stream"],
            },
        ],
        "historical_value_stream_support": [{"entity_name": "Self Stream"}],
        "historical_source": "summary_faiss",
    }

    filtered = filter_historical_result(result, ["idmt-19761"])

    assert [hit["ticket_id"] for hit in filtered["historical_ticket_hits"]] == ["IDMT-12167"]
    assert [row["entity_name"] for row in filtered["historical_value_stream_support"]] == [
        "Neighbor Stream"
    ]
