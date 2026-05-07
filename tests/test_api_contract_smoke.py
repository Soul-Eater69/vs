from __future__ import annotations

from fastapi.testclient import TestClient
from pathlib import Path
from pydantic import ValidationError
import asyncio
import shutil

from vs_app.api.routes import rag
from vs_app.api.routes import idea_cards
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.main import create_app


def test_api_routes_expose_historical_rag_only() -> None:
    app = create_app()
    routes = {route.path for route in app.routes}

    assert "/health" in routes
    assert "/api/idea-cards" in routes
    assert "/api/idea-cards/extract" in routes
    assert "/ingestion/tickets/{ticket_id}" in routes
    assert "/rag/value-streams" in routes
    assert "/rag/value-streams/plain" not in routes
    assert "/rag/value-streams/semantic" not in routes
    assert "/rag/value-streams/combined" not in routes


def test_rag_public_schema_requires_query_input() -> None:
    assert not hasattr(ValueStreamRagRequest(ticket_id="IDMT-123"), "mode")
    assert ValueStreamRagRequest(ticket_id="IDMT-123").exclude_source_ticket_from_historical is True

    try:
        ValueStreamRagRequest()
        raise AssertionError("Expected validation error")
    except ValidationError:
        pass


def test_rag_api_defaults_historical_backend_and_truth_to_azure(monkeypatch) -> None:
    assert rag._HISTORICAL_BACKEND == "azure"

    monkeypatch.setattr(rag, "_GROUND_TRUTH_SOURCE", "azure")
    monkeypatch.setattr(
        rag,
        "load_historical_summary_rows",
        lambda **kwargs: [
            {"ticket_id": "IDMT-123", "value_stream_names": ["Issue Payment"]},
        ],
    )

    assert rag._ground_truth_for_ticket("IDMT-123") == ["Issue Payment"]


def test_idea_cards_list_and_text_extract(monkeypatch) -> None:
    scratch = Path("pytest-cache-files-api-idea-cards-test")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    try:
        (scratch / "IDMT-1.txt").write_text("hello idea card", encoding="utf-8")
        monkeypatch.setattr(idea_cards, "IDEA_CARDS_DIR", scratch)

        client = TestClient(create_app())

        list_response = client.get("/api/idea-cards")
        assert list_response.status_code == 200
        assert list_response.json()["cards"][0]["doc_id"] == "IDMT-1"

        extract_response = client.post(
            "/api/idea-cards/extract?filename=upload.txt",
            content=b"uploaded idea card",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert extract_response.status_code == 200
        assert extract_response.json()["text"] == "uploaded idea card"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_condense_fallback_returns_cleaned_text_on_timeout(monkeypatch) -> None:
    async def run() -> tuple[str, list[str]]:
        def slow_condense(_raw: str) -> str:
            raise TimeoutError("slow")

        return await rag._condense_or_fallback(slow_condense, "raw", "cleaned query")

    condensed, warnings = asyncio.run(run())

    assert condensed == "cleaned query"
    assert "using cleaned idea-card text" in warnings[0]
