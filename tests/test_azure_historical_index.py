from __future__ import annotations

from vs_app.ingestion.persistence.azure_historical_index import (
    build_historical_azure_documents,
    clear_historical_summary_index,
    load_historical_summary_rows,
    search_historical_summaries,
    send_historical_documents,
    upload_historical_summary_index,
)


class FakeEmbedding:
    dimension = 3

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [1.0, 2.0, 3.0]


def test_build_historical_azure_documents_maps_summary_fields() -> None:
    docs, skipped = build_historical_azure_documents(
        [
            {
                "ticket_id": "IDMT-1",
                "summary_text": "summary",
                "business_problem": "problem",
                "business_capability": "capability",
                "value_stream_names": ["Issue Payment"],
                "value_stream_ids": ["VSR00074596"],
                "jira_group_ids": ["GROUP-1"],
                "direct_vs_names": ["Issue Payment"],
                "implied_vs_names": [],
                "value_streams": [
                    {
                        "vs_id": "VSR00074596",
                        "vs_name": "Issue Payment",
                        "jira_group_id": "GROUP-1",
                        "inference_type": "direct",
                        "reason": "Payment workflow is explicit.",
                    }
                ],
                "label_source": "jira_implemented_by_group_links",
            }
        ],
        embedding=FakeEmbedding(),
    )

    assert skipped == []
    assert docs[0]["ticket_id"] == "IDMT-1"
    assert docs[0]["content_vector"] == [1.0, 2.0, 3.0]
    assert docs[0]["value_stream_names"] == ["Issue Payment"]
    assert docs[0]["value_stream_ids"] == ["VSR00074596"]
    assert docs[0]["jira_group_ids"] == ["GROUP-1"]
    assert docs[0]["direct_vs_names"] == ["Issue Payment"]
    assert "Payment workflow is explicit." in docs[0]["value_streams_json"]
    assert docs[0]["label_source"] == "jira_implemented_by_group_links"


def test_build_historical_azure_documents_reuses_existing_summary_embedding() -> None:
    embedding = FakeEmbedding()

    docs, skipped = build_historical_azure_documents(
        [
            {
                "ticket_id": "IDMT-1",
                "summary_text": "summary",
                "business_problem": "problem",
                "business_capability": "capability",
                "summary_embedding": [0.1, "0.2", 0.3],
            }
        ],
        embedding=embedding,
    )

    assert skipped == []
    assert docs[0]["content_vector"] == [0.1, 0.2, 0.3]
    assert embedding.calls == []


def test_upload_historical_summary_index_uses_provided_summaries(monkeypatch) -> None:
    captured: dict = {}

    def fake_send(**kwargs):
        captured.update(kwargs)
        return {"success": True, "uploaded_count": len(kwargs["documents"])}

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.send_historical_documents",
        fake_send,
    )

    result = upload_historical_summary_index(
        summaries=[
            {
                "ticket_id": "IDMT-1",
                "summary_text": "summary",
                "business_problem": "problem",
                "business_capability": "capability",
                "summary_embedding": [0.1, 0.2, 0.3],
            }
        ],
        output_dir="does-not-matter",
        index_name="hist",
        embedding=FakeEmbedding(),
        document_action="upload",
        batch_size=7,
    )

    assert result["source_summary_count"] == 1
    assert result["summary_doc_count"] == 1
    assert captured["index_name"] == "hist"
    assert captured["documents"][0]["ticket_id"] == "IDMT-1"
    assert captured["documents"][0]["content_vector"] == [0.1, 0.2, 0.3]
    assert captured["action"] == "upload"
    assert captured["batch_size"] == 7


def test_upload_historical_summary_index_can_recreate_index(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.recreate_historical_summary_index",
        lambda **kwargs: calls.append(("recreate", kwargs)),
    )
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.ensure_historical_summary_index",
        lambda **kwargs: calls.append(("ensure", kwargs)),
    )
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.send_historical_documents",
        lambda **kwargs: {"success": True},
    )

    result = upload_historical_summary_index(
        summaries=[
            {
                "ticket_id": "IDMT-1",
                "summary_text": "summary",
                "business_problem": "problem",
                "business_capability": "capability",
                "summary_embedding": [0.1, 0.2, 0.3],
            }
        ],
        index_name="hist",
        embedding=FakeEmbedding(),
        recreate_index=True,
        create_index=True,
    )

    assert result["recreated_index"] is True
    assert calls == [
        (
            "recreate",
            {
                "index_name": "hist",
                "vector_dimensions": 3,
            },
        )
    ]


def test_search_historical_summaries_maps_azure_rows(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, index_name: str) -> None:
            self.index_name = index_name

        def search_hybrid(self, query: str, **kwargs):
            assert query == "condensed query"
            assert kwargs["vector_field"] == "content_vector"
            return [
                {
                    "@search.score": 0.88,
                    "ticket_id": "IDMT-1",
                    "content": "historical summary",
                    "value_stream_names": ["Issue Payment"],
                    "value_stream_ids": ["VSR00074596"],
                    "jira_group_ids": ["GROUP-1"],
                    "direct_vs_names": ["Issue Payment"],
                    "implied_vs_names": [],
                    "value_streams_json": (
                        '[{"vs_id": "VSR00074596", "vs_name": "Issue Payment", '
                        '"jira_group_id": "GROUP-1", "inference_type": "direct", '
                        '"reason": "Payment workflow is explicit."}]'
                    ),
                    "label_source": "summary",
                }
            ]

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_search_client",
        lambda **kwargs: FakeClient(index_name=kwargs["index_name"]),
    )

    rows = search_historical_summaries("condensed query", index_name="hist", top_k=5)

    assert rows == [
        {
            "ticket_id": "IDMT-1",
            "best_score": 0.88,
            "title": "IDMT-1",
            "summary_preview": "historical summary",
            "value_stream_names": ["Issue Payment"],
            "value_stream_ids": ["VSR00074596"],
            "jira_group_ids": ["GROUP-1"],
            "direct_vs_names": ["Issue Payment"],
            "implied_vs_names": [],
            "value_streams_json": (
                '[{"vs_id": "VSR00074596", "vs_name": "Issue Payment", '
                '"jira_group_id": "GROUP-1", "inference_type": "direct", '
                '"reason": "Payment workflow is explicit."}]'
            ),
            "label_source": "summary",
        }
    ]


def test_clear_historical_summary_index_deletes_existing_ids() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.deleted: list[dict] = []

        def search_all(self, search_text: str = "*", select=None):
            assert search_text == "*"
            assert select == ["id"]
            return [{"id": "one"}, {"id": "two"}, {"id": ""}]

        def delete_documents(self, documents):
            self.deleted.extend(documents)

    client = FakeClient()

    deleted_count = clear_historical_summary_index(index_name="hist", client=client)

    assert deleted_count == 2
    assert client.deleted == [{"id": "one"}, {"id": "two"}]


def test_load_historical_summary_rows_selects_historical_fields() -> None:
    class FakeClient:
        def search_all(self, search_text: str = "*", select=None):
            assert search_text == "*"
            assert "ticket_id" in select
            assert "value_stream_names" in select
            return [{"ticket_id": "IDMT-1", "value_stream_names": ["Issue Payment"]}]

    rows = load_historical_summary_rows(index_name="hist", client=FakeClient())

    assert rows == [{"ticket_id": "IDMT-1", "value_stream_names": ["Issue Payment"]}]


def test_send_historical_documents_uses_gateway_upload(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeDocumentsClient:
        def upload_documents(self, **kwargs):
            calls.append(("upload", kwargs))
            return {"success": True, "uploaded_count": len(kwargs["documents"])}

        def update_documents(self, **kwargs):
            calls.append(("update", kwargs))
            return {"success": True}

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_documents_client",
        lambda: FakeDocumentsClient(),
    )

    result = send_historical_documents(
        index_name="hist",
        documents=[{"id": "one"}],
        action="upload",
        batch_size=25,
    )

    assert result == {"success": True, "uploaded_count": 1}
    assert calls == [
        (
            "upload",
            {
                "index_name": "hist",
                "documents": [{"id": "one"}],
                "batch_size": 25,
            },
        )
    ]


def test_send_historical_documents_uses_gateway_update(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeDocumentsClient:
        def upload_documents(self, **kwargs):
            calls.append(("upload", kwargs))
            return {"success": True}

        def update_documents(self, **kwargs):
            calls.append(("update", kwargs))
            return {"success": True, "updated_count": len(kwargs["documents"])}

    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_documents_client",
        lambda: FakeDocumentsClient(),
    )

    result = send_historical_documents(
        index_name="hist",
        documents=[{"id": "one"}],
        action="update",
        batch_size=50,
    )

    assert result == {"success": True, "updated_count": 1}
    assert calls == [
        (
            "update",
            {
                "index_name": "hist",
                "documents": [{"id": "one"}],
                "batch_size": 50,
            },
        )
    ]


def test_send_historical_documents_falls_back_to_direct_upload(monkeypatch) -> None:
    class BrokenDocumentsClient:
        def upload_documents(self, **kwargs):
            raise RuntimeError("gateway down")

        def update_documents(self, **kwargs):
            raise AssertionError("should not update")

    class FakeSearchClient:
        def __init__(self) -> None:
            self.uploaded: list[dict] = []

        def upload_documents(self, documents):
            self.uploaded.extend(documents)
            return [{"succeeded": True}]

    search_client = FakeSearchClient()
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_documents_client",
        lambda: BrokenDocumentsClient(),
    )
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_search_client",
        lambda **kwargs: search_client,
    )

    result = send_historical_documents(
        index_name="hist",
        documents=[{"id": "one"}],
        action="upload",
    )

    assert search_client.uploaded == [{"id": "one"}]
    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["fallback_action"] == "upload_documents"
    assert result["gateway_error"] == "gateway down"


def test_send_historical_documents_falls_back_to_direct_merge_or_upload(monkeypatch) -> None:
    class BrokenDocumentsClient:
        def upload_documents(self, **kwargs):
            raise AssertionError("should not upload")

        def update_documents(self, **kwargs):
            raise RuntimeError("gateway update down")

    class FakeSearchClient:
        def __init__(self) -> None:
            self.merged: list[dict] = []

        def merge_or_upload_documents(self, documents):
            self.merged.extend(documents)
            return [{"succeeded": True}]

        def upload_documents(self, documents):
            raise AssertionError("update fallback should merge_or_upload")

    search_client = FakeSearchClient()
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_documents_client",
        lambda: BrokenDocumentsClient(),
    )
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index._make_search_client",
        lambda **kwargs: search_client,
    )

    result = send_historical_documents(
        index_name="hist",
        documents=[{"id": "one"}],
        action="update",
    )

    assert search_client.merged == [{"id": "one"}]
    assert result["success"] is True
    assert result["fallback_used"] is True
    assert result["fallback_action"] == "merge_or_upload_documents"
    assert result["gateway_error"] == "gateway update down"
