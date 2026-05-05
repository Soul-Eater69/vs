from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.modules.rag.service import ValueStreamRagCommand

router = APIRouter(prefix="/rag", tags=["rag"])

_FAISS_DIR = Path(os.environ.get("HISTORICAL_FAISS_DIR", "ticket_data/_faiss"))


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


def _source_ticket_exclusions(request: ValueStreamRagRequest) -> list[str] | None:
    if not request.exclude_source_ticket_from_historical or not request.ticket_id:
        return None
    return [request.ticket_id]


@router.post("/value-streams/stream")
async def predict_value_streams_stream(
    request: ValueStreamRagRequest,
) -> StreamingResponse:
    async def generate():
        from vs_app.modules.rag.query.views import condense_idea_card, clean_ppt_text
        from vs_app.modules.rag.retrieval.semantic_retriever import retrieve_semantic_candidates
        from vs_app.modules.rag.retrieval.historical_retriever import (
            filter_historical_result,
            retrieve_historical_support,
        )
        from vs_app.modules.rag.augmentation.candidate_merger import merge_candidate_sources
        from vs_app.modules.rag.augmentation.finalizer import generate_value_streams
        from vs_app.modules.rag.fingerprints import build_rag_debug_fingerprints

        try:
            top_k = max(request.top_k_historical, request.top_k_value_streams)
            top_k_vs = min(max(12, top_k), 50)
            max_llm_candidates = min(max(top_k_vs + 15, 40), 50)
            faiss_dir = str(_FAISS_DIR)

            # Step 1: Extract
            yield _sse("step", {"step": "extract", "label": f"Reading {request.ticket_id or 'idea card'}..."})
            if request.idea_card_text:
                raw_text = request.idea_card_text
            elif request.ticket_id:
                from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text
                raw_text = await asyncio.to_thread(extract_idea_card_text, doc_id=request.ticket_id)
            else:
                raise ValueError("No idea card text or ticket ID provided")

            # Step 2: Condense once. The same condensed query feeds semantic VS and historical retrieval.
            yield _sse("step", {"step": "condense", "label": "Condensing idea card for retrieval..."})
            cleaned_query = await asyncio.to_thread(clean_ppt_text, raw_text)
            query_for_prompt = await asyncio.to_thread(condense_idea_card, raw_text)
            retrieval_query = query_for_prompt or cleaned_query

            # Step 3: Semantic VS + historical FAISS both use the condensed query.
            yield _sse("step", {"step": "semantic", "label": "Searching VS and history from condensed query..."})
            exclude_ids = _source_ticket_exclusions(request)
            historical_kwargs = {
                "historical_faiss_dir": faiss_dir,
                "max_ticket_hits": min(max(12, top_k), 40),
            }
            if "exclude_ticket_ids" in inspect.signature(retrieve_historical_support).parameters:
                historical_kwargs["exclude_ticket_ids"] = exclude_ids
            semantic_task = asyncio.create_task(
                asyncio.to_thread(
                    retrieve_semantic_candidates,
                    retrieval_query,
                    top_k=top_k_vs,
                )
            )
            historical_task = asyncio.create_task(
                asyncio.to_thread(
                    retrieve_historical_support,
                    retrieval_query,
                    **historical_kwargs,
                )
            )
            semantic_candidates, historical = await asyncio.gather(semantic_task, historical_task)
            historical = filter_historical_result(historical, exclude_ids)

            # Step 4: Merge candidates
            yield _sse("step", {"step": "merge", "label": "Merging semantic and historical candidates..."})
            augmented = merge_candidate_sources(
                semantic_candidates,
                historical.get("historical_value_stream_support", []),
                max_llm_candidates=max_llm_candidates,
            )

            # Step 5: Direct + historical LLM selection run in parallel.
            yield _sse("step", {"step": "llm_select", "label": "Running direct and historical LLM passes..."})
            generated = await asyncio.to_thread(
                generate_value_streams,
                query_for_prompt=retrieval_query,
                llm_candidates=augmented["llm_candidates"],
                auto_selected=augmented["auto_selected_value_streams"],
                historical_ticket_hits=historical.get("historical_ticket_hits", []),
            )

            # Step 6: Final response assembly
            yield _sse("step", {"step": "finalize", "label": "Finalizing selections..."})
            debug = build_rag_debug_fingerprints(
                cleaned_query=cleaned_query,
                query_for_prompt=query_for_prompt,
                semantic_candidates=semantic_candidates,
                historical_support=historical.get("historical_value_stream_support", []),
                merged_candidates=augmented["merged_candidates"],
                llm_candidates=generated["candidates_used"],
                llm_selected=generated["llm_selected_value_streams"],
                final_selected=generated["selected_value_streams"],
            )

            result_payload = {
                "selected_value_streams": generated["selected_value_streams"],
                "auto_selected_value_streams": augmented["auto_selected_value_streams"],
                "llm_selected_value_streams": generated["llm_selected_value_streams"],
                "rescued_confirmed_merged_value_streams": generated.get(
                    "rescued_confirmed_merged_value_streams",
                    [],
                ),
                "rescued_historical_gap_fill_value_streams": generated.get(
                    "rescued_historical_gap_fill_value_streams",
                    [],
                ),
                "dropped_historical_gap_fill_value_streams": generated.get(
                    "dropped_historical_gap_fill_value_streams",
                    [],
                ),
                "rejected_candidates": [],
                "semantic_candidate_value_streams": semantic_candidates,
                "historical_candidate_value_streams": historical.get("historical_value_stream_support", []),
                "merged_candidate_value_streams": augmented["merged_candidates"],
                "historical_ticket_hits": historical.get("historical_ticket_hits", []),
                "historical_value_stream_support": historical.get("historical_value_stream_support", []),
                "candidate_value_streams": augmented["merged_candidates"],
                "llm_candidates": generated["candidates_used"],
                "historical_source": historical.get("historical_source", ""),
                "raw_response": generated["raw_response"],
                "direct_llm_output": (
                    generated["raw_response"].get("direct_pass")
                    if isinstance(generated.get("raw_response"), dict)
                    else None
                ),
                "historical_llm_output": (
                    generated["raw_response"].get("historical_gap_pass")
                    if isinstance(generated.get("raw_response"), dict)
                    else None
                ),
                "query_preparation": {
                    "cleaned_query": cleaned_query,
                    "query_for_prompt": query_for_prompt,
                },
                "warnings": [],
                "evidence": historical.get("historical_value_stream_support", []),
                "debug": debug,
                "historical_excluded_ticket_ids": exclude_ids or [],
                "ground_truth": _ground_truth_from_faiss(request.ticket_id) if request.ticket_id else [],
            }
            yield _sse("result", result_payload)

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
    command = ValueStreamRagCommand(
        ticket_id=request.ticket_id,
        idea_card_text=request.idea_card_text,
        source=request.source,
        fetch_count=max(request.top_k_historical, request.top_k_value_streams),
        top_k_historical=request.top_k_historical,
        top_k_value_streams=request.top_k_value_streams,
        use_llm_finalizer=request.use_llm_finalizer,
        exclude_source_ticket_from_historical=request.exclude_source_ticket_from_historical,
    )
    result = await container.rag.analyze(command)
    response = ValueStreamRagResponse.from_result(result)
    if request.ticket_id:
        response.ground_truth = _ground_truth_from_faiss(request.ticket_id)
    return response
