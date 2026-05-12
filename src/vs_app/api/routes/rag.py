from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.integrations.files.idea_card_extractor import build_foundational_metadata
from vs_app.ingestion.persistence.azure_historical_index import load_historical_summary_rows
from vs_app.modules.value_streams.canonical import canonicalize_foundational_mentions
from vs_app.modules.rag.service import ValueStreamRagCommand

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


def _foundational_metadata_from_request(request: ValueStreamRagRequest, raw_text: str) -> dict:
    if (
        request.foundational_value_streams_raw
        or request.foundational_value_streams_canonical
        or request.foundational_value_stream_entity_ids
    ):
        raw_values = list(request.foundational_value_streams_raw or [])
        canonical_values = list(request.foundational_value_streams_canonical or [])
        entity_ids = list(request.foundational_value_stream_entity_ids or [])
        return {
            "foundational_value_streams_raw": raw_values,
            "foundational_value_streams_canonical": canonical_values,
            "foundational_value_stream_entity_ids": entity_ids,
            "foundational_value_stream_matches": _foundational_matches_from_metadata(
                raw_values,
                canonical_values,
                entity_ids,
            ),
        }
    return build_foundational_metadata(raw_text)


def _foundational_matches_from_metadata(
    raw_values: list[str],
    canonical_values: list[str],
    entity_ids: list[str],
) -> list[dict]:
    matches: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for item in canonicalize_foundational_mentions(raw_values):
        key = (item.raw, item.canonical_name)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            {
                "raw": item.raw,
                "canonical_name": item.canonical_name,
                "entity_id": item.entity_id,
                "match_type": item.match_type,
            }
        )

    for idx, canonical_name in enumerate(canonical_values):
        clean = str(canonical_name or "").strip()
        if not clean:
            continue
        key = (clean, clean)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            {
                "raw": clean,
                "canonical_name": clean,
                "entity_id": entity_ids[idx] if idx < len(entity_ids) else None,
                "match_type": "canonical",
            }
        )

    return matches


def _raw_text_for_foundational_metadata(request: ValueStreamRagRequest) -> str:
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
            "Could not extract idea-card text for foundational metadata: ticket_id=%s error=%s",
            request.ticket_id,
            exc,
        )
        return ""


def _command_from_request(request: ValueStreamRagRequest) -> ValueStreamRagCommand:
    raw_text = _raw_text_for_foundational_metadata(request)
    foundational_metadata = _foundational_metadata_from_request(request, raw_text)
    return ValueStreamRagCommand(
        ticket_id=request.ticket_id,
        idea_card_text=request.idea_card_text or raw_text or None,
        semantic_fetch_k=request.semantic_fetch_k,
        historical_ticket_fetch_k=request.historical_ticket_fetch_k,
        llm_candidate_window=request.llm_candidate_window,
        final_output_count=request.final_output_count,
        foundational_value_streams_raw=foundational_metadata.get("foundational_value_streams_raw"),
        foundational_value_streams_canonical=foundational_metadata.get(
            "foundational_value_streams_canonical"
        ),
        foundational_value_stream_entity_ids=foundational_metadata.get(
            "foundational_value_stream_entity_ids"
        ),
        foundational_value_stream_matches=foundational_metadata.get(
            "foundational_value_stream_matches"
        ),
        historical_search_backend=_HISTORICAL_BACKEND,
        historical_azure_index_name=_HISTORICAL_AZURE_INDEX,
        exclude_source_ticket_from_historical=request.exclude_source_ticket_from_historical,
    )


def _response_from_result(result: object, request: ValueStreamRagRequest) -> ValueStreamRagResponse:
    response = ValueStreamRagResponse.from_result(result)
    if request.ticket_id:
        response.ground_truth = _ground_truth_for_ticket(request.ticket_id)
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
            yield _sse("step", {"step": "rag", "label": "Running shared RAG pipeline..."})
            result = await container.rag.analyze(command)
            yield _sse("step", {"step": "finalize", "label": "Finalizing selections..."})
            yield _sse("result", _response_from_result(result, request).model_dump())

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
    return _response_from_result(result, request)
