from __future__ import annotations

from fastapi.testclient import TestClient
from pathlib import Path
from pydantic import ValidationError
from types import SimpleNamespace
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
    assert ValueStreamRagRequest(ticket_id="IDMT-123").include_stage_predictions is False
    assert ValueStreamRagRequest(ticket_id="IDMT-123").extraction_backend == "current"

    try:
        ValueStreamRagRequest()
        raise AssertionError("Expected validation error")
    except ValidationError:
        pass


def test_rag_request_clamps_final_output_count() -> None:
    assert ValueStreamRagRequest(idea_card_text="hello", final_output_count=100).final_output_count == 25
    assert ValueStreamRagRequest(idea_card_text="hello", final_output_count=0).final_output_count == 1
    assert (
        ValueStreamRagRequest(
            idea_card_text="hello",
            source_ticket_title="CP 2025 Health Management & Advocacy: Digital GTM",
        ).source_ticket_title
        == "CP 2025 Health Management & Advocacy: Digital GTM"
    )


def test_rag_request_rejects_label_injection_fields() -> None:
    removed_field = "found" + "ational_" + "value_streams_canonical"
    try:
        ValueStreamRagRequest(
            idea_card_text="hello",
            **{removed_field: ["Order to Cash for Group Coverage"]},
        )
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
        assert extract_response.json()["rag_text"] == "uploaded idea card"
        assert extract_response.json()["rag_char_count"] == len("uploaded idea card")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_idea_card_extract_response_uses_unstructured_markdown_for_rag_text(monkeypatch) -> None:
    markdown = "[NarrativeText | page 1]\nStructured body"

    monkeypatch.setattr(
        idea_cards,
        "extract_uploaded_file_text",
        lambda *args, **kwargs: {
            "backend": "unstructured",
            "filename": "upload.pdf",
            "text": "Structured body",
            "markdown": markdown,
            "metadata": {"chars": len("Structured body"), "words": 2, "element_count": 1},
        },
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/idea-cards/extract?filename=upload.pdf&extraction_backend=unstructured",
        content=b"pdf bytes",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "Structured body"
    assert payload["markdown"] == markdown
    assert payload["rag_text"] == markdown
    assert payload["rag_char_count"] == len(markdown)
    assert payload["extraction_debug"]["rag_input_kind"] == "markdown"


def test_idea_card_extract_response_uses_current_text_for_rag_text(monkeypatch) -> None:
    monkeypatch.setattr(
        idea_cards,
        "extract_uploaded_file_text",
        lambda *args, **kwargs: {
            "backend": "current",
            "filename": "upload.pdf",
            "text": "Current text",
            "markdown": "[Slide]\nCurrent text",
            "metadata": {"chars": len("Current text"), "words": 2, "element_count": 1},
        },
    )

    client = TestClient(create_app())
    response = client.post(
        "/api/idea-cards/extract?filename=upload.pdf&extraction_backend=current",
        content=b"pdf bytes",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rag_text"] == "Current text"
    assert payload["rag_char_count"] == len("Current text")
    assert payload["extraction_debug"]["rag_input_kind"] == "text"


def test_local_ticket_unstructured_extraction_uses_markdown_for_rag_text(monkeypatch) -> None:
    scratch = Path("pytest-cache-files-local-rag-extract-test")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    try:
        (scratch / "IDMT-77.pdf").write_bytes(b"pdf bytes")
        markdown = "[Title | page 1]\nStructured local body"

        monkeypatch.setattr(rag, "_IDEA_CARDS_DIR", scratch)
        monkeypatch.setattr(
            rag,
            "extract_uploaded_file_text",
            lambda *args, **kwargs: {
                "backend": "unstructured",
                "filename": "IDMT-77.pdf",
                "text": "Structured local body",
                "markdown": markdown,
                "metadata": {"chars": len("Structured local body"), "words": 3, "element_count": 1},
            },
        )

        text, debug = rag._idea_card_text_from_request(
            ValueStreamRagRequest(ticket_id="IDMT-77", extraction_backend="unstructured")
        )

        assert text == markdown
        assert debug["rag_input_kind"] == "markdown"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_non_stream_ticket_only_uses_raw_text_without_label_extraction(monkeypatch) -> None:
    request = ValueStreamRagRequest(ticket_id="IDMT-1", final_output_count=5)
    raw_text = "Idea card business text"
    captured = {}

    monkeypatch.setattr(
        "vs_app.integrations.files.idea_card_extractor.extract_idea_card_text",
        lambda **kwargs: raw_text,
    )
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])
    monkeypatch.setattr(
        rag,
        "predict_stages",
        lambda **kwargs: {
            "stage_predictions": [
                {
                    "value_stream_id": "VSR00074590",
                    "value_stream_name": "Establish Product Offering",
                    "stage_scope": "broad_or_unclear",
                    "selected_stages": [],
                    "reason": "test",
                }
            ],
            "stage_candidate_debug": [],
        },
    )

    class FakeRag:
        async def analyze(self, command):
            captured["command"] = command
            return SimpleNamespace()

    response = asyncio.run(
        rag.predict_value_streams(
            request,
            container=SimpleNamespace(rag=FakeRag()),
        )
    )

    command = captured["command"]
    assert command.idea_card_text == raw_text
    assert not hasattr(command, "found" + "ational_" + "value_streams_canonical")
    assert not hasattr(response, "found" + "ational_" + "value_stream_matches")


def test_response_adds_theme_payloads_after_prediction(monkeypatch) -> None:
    request = ValueStreamRagRequest(
        ticket_id="IDMT-123",
        idea_card_text="Idea text",
        source_ticket_title="CP 2025 Health Management & Advocacy: Digital GTM",
    )
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])

    result = SimpleNamespace(
        selected_value_streams=[
            {
                "entity_id": "VSR00074590",
                "entity_name": "Establish Product Offering",
                "confidence": 0.82,
                "selection_source": "llm_pick",
            }
        ],
        query_preparation={"source_ticket_title": "Ignored because request wins"},
    )

    response = rag._response_from_result(result, request)

    assert response.source_ticket_title == "CP 2025 Health Management & Advocacy: Digital GTM"
    assert response.theme_payloads == [
        {
            "identity_key": "IDMT-123::vs_id::vsr00074590",
            "source_ticket_id": "IDMT-123",
            "source_ticket_title": "CP 2025 Health Management & Advocacy: Digital GTM",
            "theme_prefix": "CP 2025 Health Management & Advocacy: Digital GTM",
            "value_stream_entity_id": "VSR00074590",
            "value_stream_name": "Establish Product Offering",
            "theme_title": (
                "CP 2025 Health Management & Advocacy: Digital GTM - "
                "Establish Product Offering"
            ),
            "confidence": 0.82,
            "title_source": "clean_source_ticket_title",
            "selection_source": "llm_pick",
        }
    ]
    assert response.stage_predictions == []
    assert response.stage_candidate_debug == []
    assert response.debug["stage_prediction_enabled"] is False
    assert response.debug["stage_prediction_seconds"] == 0.0


def test_response_includes_request_extraction_debug(monkeypatch) -> None:
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])
    request = ValueStreamRagRequest(
        idea_card_text="Idea text",
        extraction_backend="auto",
        extraction_debug={
            "backend_requested": "auto",
            "backend_used": "unstructured",
            "chars": 123,
        },
    )

    response = rag._response_from_result(SimpleNamespace(), request)

    assert response.debug["extraction_debug"]["backend_requested"] == "auto"
    assert response.debug["extraction_debug"]["backend_used"] == "unstructured"
    assert response.debug["extraction_debug"]["chars"] == 123
    assert response.debug["stage_prediction_enabled"] is False


def test_response_runs_stage_prediction_only_when_requested(monkeypatch) -> None:
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])
    calls: list[dict] = []

    def fake_predict_stages(**kwargs):
        calls.append(kwargs)
        return {
            "stage_predictions": [
                {
                    "value_stream_id": "VSR00074590",
                    "value_stream_name": "Establish Product Offering",
                    "stage_scope": "entire_value_stream",
                    "selected_stages": [],
                    "reason": "Spans the full lifecycle.",
                }
            ],
            "stage_candidate_debug": [{"value_stream_id": "VSR00074590"}],
        }

    monkeypatch.setattr(rag, "predict_stages", fake_predict_stages)
    result = SimpleNamespace(
        selected_value_streams=[
            {
                "entity_id": "VSR00074590",
                "entity_name": "Establish Product Offering",
                "confidence": 0.82,
                "selection_source": "llm_pick",
            }
        ],
        query_preparation={
            "source_ticket_title": "Ignored because request wins",
            "query_for_prompt": "Condensed idea card",
        },
    )

    off_response = rag._response_from_result(
        result,
        ValueStreamRagRequest(
            ticket_id="IDMT-123",
            idea_card_text="Idea text",
            source_ticket_title="CP 2025 Health Management & Advocacy: Digital GTM",
        ),
    )

    assert calls == []
    assert off_response.stage_predictions == []
    assert off_response.stage_candidate_debug == []
    assert off_response.debug["stage_prediction_enabled"] is False
    assert off_response.debug["stage_prediction_seconds"] == 0.0

    on_response = rag._response_from_result(
        result,
        ValueStreamRagRequest(
            ticket_id="IDMT-123",
            idea_card_text="Idea text",
            source_ticket_title="CP 2025 Health Management & Advocacy: Digital GTM",
            include_stage_predictions=True,
        ),
    )

    assert len(calls) == 1
    assert calls[0]["condensed_idea_card"] == "Condensed idea card"
    assert on_response.stage_predictions[0]["stage_scope"] == "entire_value_stream"
    assert on_response.stage_candidate_debug == [{"value_stream_id": "VSR00074590"}]
    assert on_response.debug["stage_prediction_enabled"] is True
    assert on_response.debug["stage_prediction_seconds"] >= 0.0


def test_stream_route_emits_pipeline_progress_events(monkeypatch) -> None:
    request = ValueStreamRagRequest(idea_card_text="Idea card business text", final_output_count=5)
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])

    class FakeRag:
        async def analyze(self, command):
            assert command.progress_callback is not None
            command.progress_callback("condense", "Condensing idea card...")
            command.progress_callback("semantic", "Searching value-stream catalog...")
            command.progress_callback("historical", "Searching historical analogs...")
            command.progress_callback("merge", "Merging candidates...")
            command.progress_callback("llm_select", "Running Review Pool LLM...")
            command.progress_callback("finalize", "Finalizing selections...")
            return SimpleNamespace()

    async def collect_events() -> str:
        response = await rag.predict_value_streams_stream(
            request,
            container=SimpleNamespace(rag=FakeRag()),
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return "".join(chunks)

    body = asyncio.run(collect_events())

    for step in ["extract", "condense", "semantic", "historical", "merge", "llm_select", "finalize"]:
        assert f'"step": "{step}"' in body
    assert "Building Jira theme titles..." in body
    assert "Building Jira titles and selecting stages..." not in body
    assert 'event: result' in body


def test_stream_route_stage_progress_label_when_enabled(monkeypatch) -> None:
    request = ValueStreamRagRequest(
        idea_card_text="Idea card business text",
        final_output_count=5,
        include_stage_predictions=True,
    )
    monkeypatch.setattr(rag, "_ground_truth_for_ticket", lambda ticket_id: [])

    class FakeRag:
        async def analyze(self, command):
            return SimpleNamespace()

    async def collect_events() -> str:
        response = await rag.predict_value_streams_stream(
            request,
            container=SimpleNamespace(rag=FakeRag()),
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return "".join(chunks)

    body = asyncio.run(collect_events())

    assert "Building Jira titles and selecting stages..." in body
