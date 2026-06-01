"""Tests for the Theme-generation POC uploader (fakes only; no Azure)."""

from __future__ import annotations

from vs_app.ingestion.upload.uploader import (
    embed_idmt_documents,
    read_jsonl,
    summarize_documents,
    upload_theme_generation_documents,
    write_jsonl,
)

INDEX = "idp_theme_generation_poc"
IDMT = {
    "id": "idmt::T1",
    "document_type": "idmt",
    "ticket_id": "T1",
    "group_id": "",
    "content": "idmt content",
    "properties": {"summary_text": "s"},
}
THEME = {
    "id": "theme::T1::G1",
    "document_type": "theme",
    "ticket_id": "T1",
    "group_id": "G1",
    "content": "theme content",
    "properties": {"theme_description": "t"},
}


class FakeDocsClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def upload_documents(self, *, index_name, documents, batch_size):
        self.calls.append((index_name, documents, batch_size))
        return {"ok": True, "count": len(documents)}


class FakeEmbed:
    model = "fake-embedding-model"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed_many(self, texts):
        self.batches.append(list(texts))
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def test_dry_run_makes_no_azure_calls() -> None:
    fake = FakeDocsClient()
    res = upload_theme_generation_documents(
        docs=[IDMT, THEME], index_name=INDEX, dry_run=True, documents_client=fake
    )
    assert res["dry_run"] is True
    assert res["uploaded"] is False
    assert fake.calls == []
    assert res["total"] == 2 and res["idmt"] == 1 and res["theme"] == 1


def test_upload_flag_calls_documents_client() -> None:
    fake = FakeDocsClient()
    res = upload_theme_generation_documents(
        docs=[IDMT, THEME], index_name=INDEX, dry_run=False, documents_client=fake
    )
    assert res["uploaded"] is True
    assert len(fake.calls) == 1
    name, documents, _ = fake.calls[0]
    assert name == INDEX
    assert len(documents) == 2


def test_jsonl_round_trip(tmp_path) -> None:
    path = tmp_path / "docs.jsonl"
    write_jsonl(path, [IDMT, THEME])
    assert read_jsonl(path) == [IDMT, THEME]


def test_theme_docs_accepted_without_content_vector() -> None:
    fake = FakeDocsClient()
    upload_theme_generation_documents(
        docs=[THEME], index_name=INDEX, dry_run=False, documents_client=fake
    )
    uploaded_theme = fake.calls[0][1][0]
    assert "content_vector" not in uploaded_theme


def test_idmt_with_content_vector_passes_through() -> None:
    idmt_with_vector = {**IDMT, "content_vector": [0.5, 0.6]}
    fake = FakeDocsClient()
    upload_theme_generation_documents(
        docs=[idmt_with_vector], index_name=INDEX, dry_run=False, documents_client=fake
    )
    assert fake.calls[0][1][0]["content_vector"] == [0.5, 0.6]


def test_embed_idmt_only_embeds_idmt_documents() -> None:
    docs = [dict(IDMT), dict(THEME)]
    fake_embed = FakeEmbed()
    out = embed_idmt_documents(docs, embedding_client=fake_embed)

    idmt = next(d for d in out if d["document_type"] == "idmt")
    theme = next(d for d in out if d["document_type"] == "theme")
    assert idmt["content_vector"] == [0.1, 0.2, 0.3, 0.4]
    assert "content_vector" not in theme
    # Only the IDMT content was sent to the embedding client.
    assert fake_embed.batches == [["idmt content"]]


def test_embed_idmt_skips_already_vectored_idmt() -> None:
    docs = [{**IDMT, "content_vector": [9.0]}]
    fake_embed = FakeEmbed()
    out = embed_idmt_documents(docs, embedding_client=fake_embed)
    assert out[0]["content_vector"] == [9.0]
    assert fake_embed.batches == []  # no embedding call


def test_summarize_counts_vectors() -> None:
    summary = summarize_documents([{**IDMT, "content_vector": [0.1]}, THEME])
    assert summary == {"total": 2, "idmt": 1, "theme": 1, "with_vectors": 1}
