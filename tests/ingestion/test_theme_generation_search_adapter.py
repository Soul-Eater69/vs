"""Tests for the Theme-generation read-only search adapter (Feature 15A).

No Azure network, no embedding, no LLM. The AzureDirectSearchClient methods are
exercised by injecting a fake inner SearchClient and a tripwire embedder; the
adapter is exercised against a fake client implementing the new contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from vs_app.theme_generation.search_adapter import ThemeGenerationSearchAdapter

IDMT_FILTER = "document_type eq 'idmt'"


# --- AzureDirectSearchClient.search_by_vector / get_document_by_id -----------


class _FakeSearchClient:
    """Stands in for azure SearchClient: records search/get_document calls."""

    def __init__(self, search_results=None, documents=None) -> None:
        self.search_results = search_results or []
        self.documents = documents or {}
        self.search_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def search(self, *, search_text, vector_queries, filter, top, select):
        self.search_calls.append(
            {
                "search_text": search_text,
                "vector_queries": vector_queries,
                "filter": filter,
                "top": top,
                "select": select,
            }
        )
        return list(self.search_results)

    def get_document(self, *, key, selected_fields=None):
        self.get_calls.append({"key": key, "selected_fields": selected_fields})
        if key in self.documents:
            return self.documents[key]
        from azure.core.exceptions import ResourceNotFoundError

        raise ResourceNotFoundError("not found")


class _TripwireEmbedder:
    def embed(self, *_a, **_k):
        raise AssertionError("search_by_vector must not embed")

    def embed_many(self, *_a, **_k):
        raise AssertionError("search_by_vector must not embed")


def _make_client(monkeypatch, fake_inner):
    """Build an AzureDirectSearchClient without touching Azure credentials/SDK."""
    from vs_app.integrations.clients import azure_direct_client as adc

    client = object.__new__(adc.AzureDirectSearchClient)
    client._index_name = "idp_theme_generation_poc"
    client._endpoint = "https://example.search.windows.net"
    client._credential = object()
    client._search_client = fake_inner
    client._embedder = _TripwireEmbedder()
    return client


def test_search_by_vector_does_not_embed_and_passes_vector(monkeypatch) -> None:
    inner = _FakeSearchClient(search_results=[{"id": "idmt::T1", "@search.score": 1.0}])
    client = _make_client(monkeypatch, inner)

    out = client.search_by_vector(
        vector=[0.1, 0.2, 0.3],
        top_k=15,
        filter_expression=IDMT_FILTER,
        select=["id", "properties"],
    )

    assert out == [{"id": "idmt::T1", "@search.score": 1.0}]
    call = inner.search_calls[0]
    assert call["filter"] == IDMT_FILTER
    assert call["top"] == 15
    assert call["select"] == ["id", "properties"]
    # the precomputed vector reached the VectorizedQuery
    assert list(call["vector_queries"][0].vector) == [0.1, 0.2, 0.3]


def test_get_document_by_id_returns_dict_and_none(monkeypatch) -> None:
    inner = _FakeSearchClient(documents={"theme::T1::G1": {"id": "theme::T1::G1", "x": 1}})
    client = _make_client(monkeypatch, inner)

    assert client.get_document_by_id(doc_id="theme::T1::G1") == {"id": "theme::T1::G1", "x": 1}
    assert client.get_document_by_id(doc_id="theme::missing::G9") is None


def test_search_by_vector_index_override_builds_temp_client(monkeypatch) -> None:
    inner = _FakeSearchClient()
    client = _make_client(monkeypatch, inner)
    built: dict = {}

    from vs_app.integrations.clients import azure_direct_client as adc

    class _TempClient(_FakeSearchClient):
        def __init__(self, *, endpoint, index_name, credential) -> None:
            super().__init__(search_results=[{"id": "override-hit"}])
            built["endpoint"] = endpoint
            built["index_name"] = index_name

    monkeypatch.setattr(adc, "SearchClient", _TempClient)

    out = client.search_by_vector(vector=[0.0], top_k=5, index_name="some-other-index")
    assert len(out) == 1 and out[0]["id"] == "override-hit"  # _collect adds @search.score
    assert built["index_name"] == "some-other-index"
    # default index client was not used
    assert inner.search_calls == []


# --- ThemeGenerationSearchAdapter -------------------------------------------


class FakeClient:
    """Implements the new AzureDirectSearchClient read-only contract."""

    def __init__(self, vector_results=None, documents=None) -> None:
        self.vector_results = vector_results or []
        self.documents = documents or {}
        self.vector_calls: list[dict] = []

    def search_by_vector(self, *, vector, top_k, filter_expression=None, select=None, index_name=None):
        self.vector_calls.append(
            {
                "vector": vector,
                "top_k": top_k,
                "filter_expression": filter_expression,
                "select": select,
                "index_name": index_name,
            }
        )
        return list(self.vector_results)

    def get_document_by_id(self, *, doc_id, select=None, index_name=None):
        return self.documents.get(doc_id)


def test_adapter_vector_search_delegates_with_filter() -> None:
    fake = FakeClient(vector_results=[{"id": "idmt::T1"}])
    adapter = ThemeGenerationSearchAdapter(client=fake, index_name="idp_theme_generation_poc")

    out = adapter.vector_search(query_vector=[0.1, 0.2], top_k=15, filter_expression=IDMT_FILTER)

    assert out == [{"id": "idmt::T1"}]
    call = fake.vector_calls[0]
    assert call["vector"] == [0.1, 0.2]
    assert call["top_k"] == 15
    assert call["filter_expression"] == IDMT_FILTER
    assert call["index_name"] == "idp_theme_generation_poc"


def test_adapter_per_call_index_override_wins() -> None:
    fake = FakeClient()
    adapter = ThemeGenerationSearchAdapter(client=fake, index_name="configured")
    adapter.vector_search(query_vector=[0.0], top_k=5, filter_expression=IDMT_FILTER, index_name="override")
    assert fake.vector_calls[0]["index_name"] == "override"


def test_adapter_get_document_found_and_missing() -> None:
    fake = FakeClient(documents={"theme::T1::G1": {"id": "theme::T1::G1"}})
    adapter = ThemeGenerationSearchAdapter(client=fake)
    assert adapter.get_document(doc_id="theme::T1::G1") == {"id": "theme::T1::G1"}
    assert adapter.get_document(doc_id="theme::missing::G9") is None


def test_adapter_is_read_only() -> None:
    # The adapter must only expose read methods; no upload/create/delete surface.
    adapter = ThemeGenerationSearchAdapter(client=FakeClient())
    public = {name for name in dir(adapter) if not name.startswith("_")}
    assert public == {"vector_search", "get_document"}
    for forbidden in ("upload_documents", "create_index", "delete_index", "merge_or_upload_documents"):
        assert not hasattr(adapter, forbidden)


def test_adapter_works_in_retrieval_pipeline() -> None:
    # End-to-end with the 14A helpers using the adapter as the injected client.
    from vs_app.theme_generation.retrieval import (
        extract_matching_theme_refs,
        fetch_theme_examples,
        search_idmt_examples,
    )

    VS = "Configure, Price, and Quote"
    idmt = {
        "document_type": "idmt",
        "ticket_id": "T1",
        "@search.score": 1.0,
        "properties": {"value_streams": [{"group_id": "G1", "value_stream_name": VS}]},
    }
    theme = {"id": "theme::T1::G1", "ticket_id": "T1", "group_id": "G1", "properties": {}}
    fake = FakeClient(vector_results=[idmt], documents={"theme::T1::G1": theme})
    adapter = ThemeGenerationSearchAdapter(client=fake)

    docs = search_idmt_examples(search_client=adapter, query_vector=[0.1], top_k=15)
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS)
    theme_docs = fetch_theme_examples(search_client=adapter, refs=refs)
    assert [d["id"] for d in theme_docs] == ["theme::T1::G1"]
