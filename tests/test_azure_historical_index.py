from __future__ import annotations

from vs_app.ingestion.persistence.azure_historical_index import (
    build_historical_azure_documents,
    clear_historical_summary_index,
    search_historical_summaries,
    send_historical_documents,
)


class FakeEmbedding:
    dimension = 3

    def embed(self, text: str) -> list[float]:
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
                "value_stream_ids": ["GROUP-1"],
                "direct_vs_names": ["Issue Payment"],
                "implied_vs_names": [],
                "label_source": "jira_implemented_by_group_links",
            }
        ],
        embedding=FakeEmbedding(),
    )

    assert skipped == []
    assert docs[0]["ticket_id"] == "IDMT-1"
    assert docs[0]["content_vector"] == [1.0, 2.0, 3.0]
    assert docs[0]["value_stream_names"] == ["Issue Payment"]
    assert docs[0]["direct_vs_names"] == ["Issue Payment"]
    assert docs[0]["label_source"] == "jira_implemented_by_group_links"


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
                    "value_stream_ids": ["GROUP-1"],
                    "direct_vs_names": ["Issue Payment"],
                    "implied_vs_names": [],
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
            "value_stream_ids": ["GROUP-1"],
            "direct_vs_names": ["Issue Payment"],
            "implied_vs_names": [],
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
