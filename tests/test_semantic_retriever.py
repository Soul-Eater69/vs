from __future__ import annotations

from vs_app.modules.rag.retrieval import semantic_retriever


def test_semantic_search_query_dedupes_and_caps_terms() -> None:
    query = " ".join(["Payment"] * 20 + [f"term{idx}" for idx in range(200)])

    compact = semantic_retriever._semantic_search_query(query)
    terms = compact.split()

    assert terms[0] == "payment"
    assert len(terms) == semantic_retriever._SEMANTIC_QUERY_MAX_TERMS
    assert len(set(terms)) == len(terms)


def test_retrieve_semantic_candidates_uses_compact_search_query(monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        def search_hybrid(self, query: str, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return [
                {
                    "entity_id": "vs-1",
                    "entity_name": "Issue Payment",
                    "content": "Processing and issuing payments.",
                    "@search.score": 1.2,
                }
            ]

    long_query = " ".join(f"token{idx}" for idx in range(500))
    rows = semantic_retriever.retrieve_semantic_candidates(long_query, top_k=5, client=FakeClient())

    assert [row["entity_name"] for row in rows] == ["Issue Payment"]
    assert len(captured["query"].split()) == semantic_retriever._SEMANTIC_QUERY_MAX_TERMS
    assert captured["kwargs"]["search_fields"] == ["entity_name", "content"]


def test_retrieve_semantic_candidates_vector_fallback_uses_compact_query(monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        def search_hybrid(self, query: str, **kwargs):
            raise RuntimeError("hybrid failed")

        def search_vector(self, query: str, **kwargs):
            captured["query"] = query
            return []

    long_query = " ".join(f"token{idx}" for idx in range(500))
    semantic_retriever.retrieve_semantic_candidates(long_query, top_k=5, client=FakeClient())

    assert len(captured["query"].split()) == semantic_retriever._SEMANTIC_QUERY_MAX_TERMS
