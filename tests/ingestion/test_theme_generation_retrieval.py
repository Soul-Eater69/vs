"""Tests for Theme-generation retrieval helpers (Feature 14A; fakes only)."""

from __future__ import annotations

from typing import Any

from vs_app.ingestion.theme_generation.retrieval import (
    IDMT_FILTER,
    extract_matching_theme_refs,
    fetch_theme_examples,
    search_idmt_examples,
    select_theme_examples_for_prompt,
)

VS = "Configure, Price, and Quote"


class FakeSearchClient:
    def __init__(self, vector_results=None, documents=None) -> None:
        self.vector_results = vector_results or []
        self.documents = documents or {}
        self.vector_calls: list[dict] = []
        self.fetched_ids: list[str] = []

    def vector_search(self, *, query_vector, top_k, filter_expression, index_name=None):
        self.vector_calls.append(
            {
                "query_vector": query_vector,
                "top_k": top_k,
                "filter_expression": filter_expression,
                "index_name": index_name,
            }
        )
        return list(self.vector_results)

    def get_document(self, *, doc_id, index_name=None):
        self.fetched_ids.append(doc_id)
        return self.documents.get(doc_id)


def _idmt(ticket_id, value_streams, *, score=1.0, document_type="idmt"):
    return {
        "id": f"idmt::{ticket_id}",
        "document_type": document_type,
        "ticket_id": ticket_id,
        "@search.score": score,
        "properties": {"value_streams": value_streams},
    }


def _vs(group_id, name, support_type="", reason="", evidence=""):
    return {
        "group_id": group_id,
        "value_stream_name": name,
        "support_type": support_type,
        "reason": reason,
        "evidence": evidence,
    }


# --- search_idmt_examples ---------------------------------------------------


def test_search_idmt_examples_filters_to_idmt() -> None:
    client = FakeSearchClient(vector_results=[_idmt("IDMT-1", [_vs("G1", VS)])])
    out = search_idmt_examples(search_client=client, query_vector=[0.1, 0.2], top_k=15)
    assert client.vector_calls[0]["filter_expression"] == IDMT_FILTER
    assert client.vector_calls[0]["top_k"] == 15
    assert client.vector_calls[0]["query_vector"] == [0.1, 0.2]
    assert [d["ticket_id"] for d in out] == ["IDMT-1"]


def test_search_idmt_examples_defensively_drops_non_idmt() -> None:
    mixed = [
        _idmt("IDMT-1", [_vs("G1", VS)]),
        {"id": "theme::IDMT-1::G1", "document_type": "theme", "ticket_id": "IDMT-1"},
    ]
    client = FakeSearchClient(vector_results=mixed)
    out = search_idmt_examples(search_client=client, query_vector=[0.0], top_k=5)
    assert all(d["document_type"] == "idmt" for d in out)
    assert len(out) == 1


# --- extract_matching_theme_refs --------------------------------------------


def test_extract_refs_matches_case_and_space_insensitively() -> None:
    docs = [_idmt("IDMT-1", [_vs("G1", "  configure,   price, AND quote ")])]
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS)
    assert [(r["ticket_id"], r["group_id"]) for r in refs] == [("IDMT-1", "G1")]


def test_extract_refs_skips_unrelated_value_streams() -> None:
    docs = [_idmt("IDMT-1", [_vs("G9", "Manage Leads and opportunities")])]
    assert extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS) == []


def test_extract_refs_dedupes_ticket_group() -> None:
    docs = [
        _idmt("IDMT-1", [_vs("G1", VS, support_type="implied")]),
        _idmt("IDMT-1", [_vs("G1", VS, support_type="implied")]),
    ]
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS)
    assert len(refs) == 1


def test_extract_refs_prefers_direct_over_implied_duplicate() -> None:
    docs = [
        _idmt("IDMT-1", [_vs("G1", VS, support_type="implied", reason="r-implied")]),
        _idmt("IDMT-1", [_vs("G1", VS, support_type="direct", reason="r-direct")]),
    ]
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS)
    assert len(refs) == 1
    assert refs[0]["support_type"] == "direct"
    assert refs[0]["reason"] == "r-direct"


def test_extract_refs_caps_to_max_refs_and_skips_missing_ids() -> None:
    docs = [
        _idmt("IDMT-1", [_vs("G1", VS)]),
        _idmt("IDMT-2", [_vs("G2", VS)]),
        _idmt("IDMT-3", [_vs("G3", VS)]),
        _idmt("", [_vs("G4", VS)]),          # missing ticket_id -> skipped
        _idmt("IDMT-5", [_vs("", VS)]),       # missing group_id -> skipped
    ]
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS, max_refs=2)
    assert [r["ticket_id"] for r in refs] == ["IDMT-1", "IDMT-2"]


def test_extract_refs_preserves_score() -> None:
    docs = [_idmt("IDMT-1", [_vs("G1", VS)], score=0.87)]
    refs = extract_matching_theme_refs(idmt_docs=docs, value_stream_name=VS)
    assert refs[0]["score"] == 0.87


# --- fetch_theme_examples ---------------------------------------------------


def _theme(ticket_id, group_id, *, theme_description="td", business_needs="bn"):
    return {
        "id": f"theme::{ticket_id}::{group_id}",
        "document_type": "theme",
        "ticket_id": ticket_id,
        "group_id": group_id,
        "properties": {
            "theme_description": theme_description,
            "business_needs": business_needs,
            "value_streams": [_vs(group_id, VS)],
            "stages": [{"stage_name": "Account Configuration"}],
        },
    }


def test_fetch_theme_examples_uses_deterministic_ids_and_order() -> None:
    docs = {
        "theme::IDMT-1::G1": _theme("IDMT-1", "G1"),
        "theme::IDMT-2::G2": _theme("IDMT-2", "G2"),
    }
    client = FakeSearchClient(documents=docs)
    refs = [
        {"ticket_id": "IDMT-2", "group_id": "G2"},
        {"ticket_id": "IDMT-1", "group_id": "G1"},
    ]
    out = fetch_theme_examples(search_client=client, refs=refs)
    assert client.fetched_ids == ["theme::IDMT-2::G2", "theme::IDMT-1::G1"]
    assert [d["id"] for d in out] == ["theme::IDMT-2::G2", "theme::IDMT-1::G1"]


def test_fetch_theme_examples_skips_missing_docs() -> None:
    client = FakeSearchClient(documents={"theme::IDMT-1::G1": _theme("IDMT-1", "G1")})
    refs = [
        {"ticket_id": "IDMT-1", "group_id": "G1"},
        {"ticket_id": "IDMT-404", "group_id": "G404"},  # missing
    ]
    out = fetch_theme_examples(search_client=client, refs=refs)
    assert [d["id"] for d in out] == ["theme::IDMT-1::G1"]


# --- select_theme_examples_for_prompt ---------------------------------------


def test_select_examples_strips_content_vector_and_keeps_compact_fields() -> None:
    doc = _theme("IDMT-1", "G1")
    doc["content_vector"] = [0.1, 0.2, 0.3]
    doc["content"] = "x" * 10000
    examples = select_theme_examples_for_prompt(theme_docs=[doc])
    assert len(examples) == 1
    ex = examples[0]
    assert "content_vector" not in ex
    assert "content" not in ex
    assert set(ex) == {"ticket_id", "group_id", "theme_description", "business_needs", "value_streams", "stages"}


def test_select_examples_skips_blank_examples() -> None:
    blank = _theme("IDMT-1", "G1", theme_description="", business_needs="")
    good = _theme("IDMT-2", "G2", theme_description="useful", business_needs="")
    examples = select_theme_examples_for_prompt(theme_docs=[blank, good])
    assert [e["ticket_id"] for e in examples] == ["IDMT-2"]


def test_select_examples_caps_to_max() -> None:
    docs = [_theme(f"IDMT-{i}", f"G{i}") for i in range(10)]
    examples = select_theme_examples_for_prompt(theme_docs=docs, max_examples=5)
    assert len(examples) == 5
