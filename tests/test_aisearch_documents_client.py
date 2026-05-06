from __future__ import annotations

from vs_app.integrations.clients import aisearch_documents
from vs_app.integrations.clients.aisearch_documents import AISearchDocumentsClient


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.body


class FakeHttpClient:
    def __init__(self, *args, **kwargs) -> None:
        self.requests: list[dict] = []

    def request(self, method: str, url: str, json: dict):
        self.requests.append({"method": method, "url": url, "json": json})
        return FakeResponse({"success": True, "uploaded_count": len(json["documents"])})


def test_aisearch_documents_client_posts_upload_payload(monkeypatch) -> None:
    fake_http = FakeHttpClient()
    monkeypatch.setattr(aisearch_documents.httpx, "Client", lambda *args, **kwargs: fake_http)

    client = AISearchDocumentsClient(
        base_url="https://idp.example",
        app_id="app",
        documents_path="/api/v1/aisearch/documents",
    )
    result = client.upload_documents(
        index_name="historical",
        documents=[{"id": "one"}],
        batch_size=10,
    )

    assert result == {"success": True, "uploaded_count": 1}
    assert fake_http.requests == [
        {
            "method": "POST",
            "url": "https://idp.example/api/v1/aisearch/documents",
            "json": {
                "index_name": "historical",
                "documents": [{"id": "one"}],
                "batch_size": 10,
            },
        }
    ]


def test_aisearch_documents_client_puts_update_payload(monkeypatch) -> None:
    fake_http = FakeHttpClient()
    monkeypatch.setattr(aisearch_documents.httpx, "Client", lambda *args, **kwargs: fake_http)

    client = AISearchDocumentsClient(
        base_url="https://idp.example/",
        app_id="app",
        documents_path="api/v1/aisearch/documents",
    )
    client.update_documents(
        index_name="historical",
        documents=[{"id": "one"}],
        batch_size=20,
    )

    assert fake_http.requests[0]["method"] == "PUT"
    assert fake_http.requests[0]["url"] == "https://idp.example/api/v1/aisearch/documents"
    assert fake_http.requests[0]["json"]["batch_size"] == 20
