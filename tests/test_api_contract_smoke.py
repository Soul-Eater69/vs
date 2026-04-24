from __future__ import annotations

from pydantic import ValidationError

from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.main import create_app


def test_api_routes_expose_historical_rag_only() -> None:
    app = create_app()
    routes = {route.path for route in app.routes}

    assert "/health" in routes
    assert "/ingestion/tickets/{ticket_id}" in routes
    assert "/rag/value-streams" in routes
    assert "/rag/value-streams/plain" not in routes
    assert "/rag/value-streams/semantic" not in routes
    assert "/rag/value-streams/combined" not in routes


def test_rag_public_schema_requires_query_input() -> None:
    assert not hasattr(ValueStreamRagRequest(ticket_id="IDMT-123"), "mode")

    try:
        ValueStreamRagRequest()
        raise AssertionError("Expected validation error")
    except ValidationError:
        pass
