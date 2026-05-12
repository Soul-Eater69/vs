from __future__ import annotations

import asyncio
import sys
from types import ModuleType

from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.modules.rag.retrieval.historical_retriever import (
    filter_historical_result,
    retrieve_historical_support,
)


def _minimal_payload() -> dict:
    return {
        "selected_value_streams": [],
        "auto_selected_value_streams": [],
        "llm_selected_value_streams": [],
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
        captured["historical_search_backend"] = kwargs.get("historical_search_backend")
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
    assert captured["historical_search_backend"] == "azure"
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


def test_source_ticket_exclusion_fetches_one_extra_summary_hit(monkeypatch) -> None:
    captured: dict = {}

    fake_store = ModuleType("vs_app.integrations.sinks.faiss_store")

    def faiss_index_exists(*, index_dir: str, kind: str) -> bool:
        return True

    def search_local_faiss(query: str, *, index_dir: str, kind: str, top_k: int) -> list[dict]:
        captured["top_k"] = top_k
        return [
            {
                "score": 0.9,
                "content": "self",
                "metadata": {
                    "ticket_id": "IDMT-19761",
                    "direct_vs_names": ["Self Stream"],
                },
            },
            {
                "score": 0.8,
                "content": "neighbor",
                "metadata": {
                    "ticket_id": "IDMT-12167",
                    "direct_vs_names": ["Neighbor Stream"],
                },
            },
        ]

    fake_store.faiss_index_exists = faiss_index_exists
    fake_store.search_local_faiss = search_local_faiss
    monkeypatch.setitem(sys.modules, "vs_app.integrations.sinks.faiss_store", fake_store)

    result = retrieve_historical_support(
        "query",
        historical_faiss_dir="unused",
        historical_search_backend="faiss",
        max_ticket_hits=30,
        exclude_ticket_ids=["IDMT-19761"],
    )

    assert captured["top_k"] == 31
    assert [hit["ticket_id"] for hit in result["historical_ticket_hits"]] == ["IDMT-12167"]


def test_azure_historical_backend_returns_support(monkeypatch) -> None:
    def fake_search(query: str, *, index_name: str, top_k: int, exclude_ticket_ids):
        assert query == "query"
        assert index_name == "historical-index"
        assert top_k == 10
        assert set(exclude_ticket_ids) == {"IDMT-1"}
        return [
            {
                "ticket_id": "IDMT-2",
                "best_score": 0.91,
                "summary_preview": "neighbor",
                "direct_vs_names": ["Issue Payment"],
                "implied_vs_names": [],
                "label_source": "summary_azure_ai_search",
            }
        ]

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.search_historical_summaries",
        fake_search,
    )

    result = retrieve_historical_support(
        "query",
        historical_search_backend="azure",
        historical_azure_index_name="historical-index",
        max_ticket_hits=10,
        exclude_ticket_ids=["IDMT-1"],
    )

    assert result["historical_source"] == "summary_azure_ai_search"
    assert [hit["ticket_id"] for hit in result["historical_ticket_hits"]] == ["IDMT-2"]
    assert [row["entity_name"] for row in result["historical_value_stream_support"]] == [
        "Issue Payment"
    ]
