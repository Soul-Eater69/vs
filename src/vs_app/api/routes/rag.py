from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.ingestion.persistence.azure_historical_index import load_historical_summary_rows
from vs_app.modules.rag.service import ValueStreamRagCommand
from vs_app.modules.stages.pipeline import predict_stages
from vs_app.modules.themes.title_builder import (
    build_theme_payloads,
    enrich_stage_predictions_with_titles,
)

router = APIRouter(prefix="/rag", tags=["rag"])
logger = logging.getLogger(__name__)

_FAISS_DIR = Path(os.environ.get("HISTORICAL_FAISS_DIR", "ticket_data/_faiss"))
_IDEA_CARDS_DIR = Path(os.environ.get("IDEA_CARDS_DIR", "idea_cards"))
_HISTORICAL_BACKEND = os.environ.get("HISTORICAL_SEARCH_BACKEND", "azure")
_HISTORICAL_AZURE_INDEX = os.environ.get(
    "HISTORICAL_AZURE_SEARCH_INDEX_NAME",
    "idp_idmt_data",
)
_GROUND_TRUTH_SOURCE = os.environ.get("RAG_GROUND_TRUTH_SOURCE", "azure")
_VALUE_STREAM_INDEX_NAME = os.environ.get(
    "VALUE_STREAM_AZURE_SEARCH_INDEX_NAME",
    os.environ.get("AZURE_SEARCH_INDEX_NAME", "value-streams"),
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _ground_truth_from_faiss(ticket_id: str) -> list[str]:
    docs_path = _FAISS_DIR / "summary_docs.json"
    if not docs_path.exists():
        return []
    try:
        docs = json.loads(docs_path.read_text(encoding="utf-8"))
        if isinstance(docs, dict):
            docs = docs.get("summaries") or []
        key = ticket_id.strip().lower()
        for doc in docs or []:
            if str(doc.get("ticket_id") or "").strip().lower() == key:
                names = (
                    doc.get("value_stream_names")
                    or doc.get("direct_vs_names")
                    or doc.get("value_stream_labels")
                    or []
                )
                return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        pass
    return []


def _ground_truth_from_azure(ticket_id: str) -> list[str]:
    key = ticket_id.strip().lower()
    if not key:
        return []
    try:
        for row in load_historical_summary_rows(index_name=_HISTORICAL_AZURE_INDEX):
            if str(row.get("ticket_id") or "").strip().lower() != key:
                continue
            names = (
                row.get("value_stream_names")
                or row.get("direct_vs_names")
                or row.get("value_stream_labels")
                or []
            )
            return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        return []
    return []


def _ground_truth_for_ticket(ticket_id: str | None) -> list[str]:
    if not ticket_id:
        return []
    source = str(_GROUND_TRUTH_SOURCE or "").strip().lower()
    if source == "faiss":
        return _ground_truth_from_faiss(ticket_id)
    return _ground_truth_from_azure(ticket_id)


def _idea_card_text_from_request(request: ValueStreamRagRequest) -> str:
    """Load the idea-card body used for prediction, without extracting labels."""
    raw_text = request.idea_card_text or ""
    if raw_text or not request.ticket_id:
        return raw_text

    try:
        from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text

        return extract_idea_card_text(
            doc_id=request.ticket_id,
            local_card_dir=_IDEA_CARDS_DIR,
        )
    except Exception as exc:
        logger.warning(
            "Could not extract idea-card text: ticket_id=%s error=%s",
            request.ticket_id,
            exc,
        )
        return ""


def _command_from_request(request: ValueStreamRagRequest) -> ValueStreamRagCommand:
    idea_card_text = _idea_card_text_from_request(request)
    return ValueStreamRagCommand(
        ticket_id=request.ticket_id,
        idea_card_text=request.idea_card_text or idea_card_text or None,
        semantic_fetch_k=request.semantic_fetch_k,
        historical_ticket_fetch_k=request.historical_ticket_fetch_k,
        llm_candidate_window=request.llm_candidate_window,
        final_output_count=request.final_output_count,
        historical_search_backend=_HISTORICAL_BACKEND,
        historical_azure_index_name=_HISTORICAL_AZURE_INDEX,
        exclude_source_ticket_from_historical=request.exclude_source_ticket_from_historical,
    )


def _response_from_result(result: object, request: ValueStreamRagRequest) -> ValueStreamRagResponse:
    response = ValueStreamRagResponse.from_result(result)
    query_preparation = response.query_preparation or {}
    source_ticket_title = (
        request.source_ticket_title
        or query_preparation.get("source_ticket_title")
        or request.ticket_id
        or ""
    )
    response.source_ticket_title = str(source_ticket_title or "").strip()
    response.theme_title_prefix = str(query_preparation.get("theme_title_prefix") or "").strip()
    response.theme_title_prefix_source = str(query_preparation.get("theme_title_prefix_source") or "").strip()
    if request.ticket_id:
        response.ground_truth = _ground_truth_for_ticket(request.ticket_id)
    if request.ticket_id and response.source_ticket_title and response.selected_value_streams:
        response.theme_payloads = build_theme_payloads(
            source_ticket_id=request.ticket_id,
            source_ticket_title=response.source_ticket_title,
            selected_value_streams=response.selected_value_streams,
            theme_title_prefix=response.theme_title_prefix,
            theme_title_prefix_source=response.theme_title_prefix_source,
        )
        if response.theme_payloads:
            first_theme = response.theme_payloads[0]
            response.theme_title_prefix = str(first_theme.get("theme_prefix") or response.theme_title_prefix)
            response.theme_title_prefix_source = str(
                first_theme.get("title_source") or response.theme_title_prefix_source
            )

    response.debug = dict(response.debug or {})
    if request.include_stage_predictions and response.selected_value_streams:
        condensed_idea_card = (
            query_preparation.get("query_for_prompt")
            or query_preparation.get("summary_text")
            or request.idea_card_text
            or ""
        )
        stage_started = perf_counter()
        stage_result = predict_stages(
            condensed_idea_card=str(condensed_idea_card),
            selected_value_streams=response.selected_value_streams,
            index_name=_VALUE_STREAM_INDEX_NAME,
        )
        stage_seconds = perf_counter() - stage_started
        response.stage_predictions = enrich_stage_predictions_with_titles(
            stage_predictions=stage_result.get("stage_predictions", []),
            theme_payloads=response.theme_payloads,
        )
        response.stage_candidate_debug = list(stage_result.get("stage_candidate_debug", []) or [])
        response.debug["stage_prediction_enabled"] = True
        response.debug["stage_prediction_seconds"] = round(stage_seconds, 3)
    else:
        response.stage_predictions = []
        response.stage_candidate_debug = []
        response.debug["stage_prediction_enabled"] = False
        response.debug["stage_prediction_seconds"] = 0.0
    return response


@router.post("/value-streams/stream")
async def predict_value_streams_stream(
    request: ValueStreamRagRequest,
    container: ApiContainer = Depends(get_container),
) -> StreamingResponse:
    async def generate():
        try:
            yield _sse("step", {"step": "extract", "label": f"Reading {request.ticket_id or 'idea card'}..."})
            command = await asyncio.to_thread(_command_from_request, request)

            loop = asyncio.get_running_loop()
            progress_events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

            def progress_callback(step: str, label: str) -> None:
                loop.call_soon_threadsafe(
                    progress_events.put_nowait,
                    ("step", {"step": step, "label": label}),
                )

            command.progress_callback = progress_callback

            async def run_analysis():
                return await asyncio.to_thread(
                    lambda: asyncio.run(container.rag.analyze(command))
                )

            task = asyncio.create_task(run_analysis())
            while not task.done():
                try:
                    event, data = await asyncio.wait_for(progress_events.get(), timeout=0.1)
                    yield _sse(event, data)
                except asyncio.TimeoutError:
                    continue

            while not progress_events.empty():
                event, data = progress_events.get_nowait()
                yield _sse(event, data)

            result = await task
            if request.include_stage_predictions:
                yield _sse(
                    "step",
                    {"step": "finalize", "label": "Building Jira titles and selecting stages..."},
                )
            else:
                yield _sse("step", {"step": "finalize", "label": "Building Jira theme titles..."})
            response = await asyncio.to_thread(_response_from_result, result, request)
            yield _sse("result", response.model_dump())

        except Exception as exc:
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/value-streams", response_model=ValueStreamRagResponse)
async def predict_value_streams(
    request: ValueStreamRagRequest,
    container: ApiContainer = Depends(get_container),
) -> ValueStreamRagResponse:
    command = _command_from_request(request)
    result = await container.rag.analyze(command)
    return await asyncio.to_thread(_response_from_result, result, request)
