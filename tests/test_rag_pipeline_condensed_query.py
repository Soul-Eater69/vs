from __future__ import annotations

from vs_app.modules.rag import pipeline


def test_condensed_query_feeds_semantic_and_historical_retrieval(monkeypatch) -> None:
    calls: dict[str, str] = {}
    progress: list[str] = []

    monkeypatch.setattr(
        "vs_app.modules.rag.query.views.clean_ppt_text",
        lambda query: "CLEANED QUERY",
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.query.views.condense_idea_card_with_metadata",
        lambda query, max_chars=3500: ("CONDENSED QUERY", {}),
    )

    def fake_semantic(query, **kwargs):
        calls["semantic"] = query
        return [{"entity_id": "VS-1", "entity_name": "Issue Payment"}]

    def fake_historical(query, **kwargs):
        calls["historical"] = query
        return {
            "historical_ticket_hits": [],
            "historical_value_stream_support": [],
            "historical_source": "summary_faiss",
        }

    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.semantic_retriever.retrieve_semantic_candidates",
        fake_semantic,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.historical_retriever.retrieve_historical_support",
        fake_historical,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.historical_retriever.filter_historical_result",
        lambda result, exclude_ticket_ids: result,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.augmentation.candidate_merger.merge_candidate_sources",
        lambda semantic, historical, **kwargs: {
            "merged_candidates": semantic,
            "llm_candidates": semantic,
            "auto_selected_value_streams": [],
        },
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.augmentation.finalizer.generate_review_pool_value_streams",
        lambda **kwargs: {
            "selected_value_streams": [],
            "llm_selected_value_streams": [],
            "candidates_used": kwargs["llm_candidates"],
            "raw_response": {},
        },
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.fingerprints.build_rag_debug_fingerprints",
        lambda **kwargs: {},
    )

    result = pipeline.select_value_streams(
        "RAW QUERY",
        progress_callback=lambda step, label: progress.append(step),
    )

    assert calls == {
        "semantic": "CONDENSED QUERY",
        "historical": "CONDENSED QUERY",
    }
    assert result["query_preparation"] == {
        "source_ticket_title": "RAW QUERY",
        "theme_title_prefix": "RAW QUERY",
        "theme_title_prefix_source": "clean_source_ticket_title",
        "cleaned_query": "CLEANED QUERY",
        "query_for_prompt": "CONDENSED QUERY",
    }
    assert progress == [
        "condense",
        "semantic",
        "historical",
        "merge",
        "llm_select",
        "finalize",
    ]


def test_pipeline_falls_back_to_semantic_candidates_when_merge_window_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "vs_app.modules.rag.query.views.clean_ppt_text",
        lambda query: "CLEANED QUERY",
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.query.views.condense_idea_card_with_metadata",
        lambda query, max_chars=3500: ("CONDENSED QUERY", {}),
    )

    def fake_semantic(query, **kwargs):
        return [
            {"entity_id": "VS-1", "entity_name": "Issue Payment", "semantic_score": 0.4},
            {"entity_id": "VS-2", "entity_name": "Manage Member Care", "semantic_score": 0.3},
        ]

    def fake_historical(query, **kwargs):
        return {
            "historical_ticket_hits": [{"ticket_id": f"IDMT-{idx}"} for idx in range(6)],
            "historical_evidence_ticket_hits": [],
            "historical_ignored_ticket_hits": [{"ticket_id": f"IDMT-{idx}"} for idx in range(6)],
            "historical_value_stream_support": [],
            "historical_source": "none_qualified",
        }

    captured: dict = {}

    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.semantic_retriever.retrieve_semantic_candidates",
        fake_semantic,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.historical_retriever.retrieve_historical_support",
        fake_historical,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.retrieval.historical_retriever.filter_historical_result",
        lambda result, exclude_ticket_ids: result,
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.augmentation.candidate_merger.merge_candidate_sources",
        lambda semantic, historical, **kwargs: {
            "merged_candidates": [],
            "llm_candidates": [],
            "auto_selected_value_streams": [],
            "candidate_window_counts": {},
        },
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.augmentation.finalizer.generate_review_pool_value_streams",
        lambda **kwargs: captured.setdefault(
            "result",
            {
                "selected_value_streams": [],
                "llm_selected_value_streams": [],
                "candidates_used": kwargs["llm_candidates"],
                "raw_response": {},
            },
        ),
    )
    monkeypatch.setattr(
        "vs_app.modules.rag.fingerprints.build_rag_debug_fingerprints",
        lambda **kwargs: {},
    )

    result = pipeline.select_value_streams("RAW QUERY", final_output_count=12)

    assert [row["entity_name"] for row in result["llm_candidates"]] == [
        "Issue Payment",
        "Manage Member Care",
    ]
    assert result["debug"]["historical_counts"] == {
        "retrieved_ticket_hits": 6,
        "qualified_ticket_hits": 0,
        "ignored_ticket_hits": 6,
        "historical_vs_support_count": 0,
    }
    assert result["candidate_window_counts"]["pipeline_semantic_fallback"] == 2
