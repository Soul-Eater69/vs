from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.ingestion.persistence.azure_historical_index import load_historical_summary_rows
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
_CONDENSE_TIMEOUT_SECONDS = float(os.environ.get("RAG_CONDENSE_TIMEOUT_SECONDS", "8"))


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


def _source_ticket_exclusions(request: ValueStreamRagRequest) -> list[str] | None:
    if not request.exclude_source_ticket_from_historical or not request.ticket_id:
        return None
    return [request.ticket_id]


async def _condense_or_fallback(condense_idea_card, raw_text: str, cleaned_query: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        condensed = await asyncio.wait_for(
            asyncio.to_thread(condense_idea_card, raw_text),
            timeout=max(1.0, _CONDENSE_TIMEOUT_SECONDS),
        )
        return condensed, warnings
    except asyncio.TimeoutError:
        warnings.append(
            f"Condense step timed out after {_CONDENSE_TIMEOUT_SECONDS:.0f}s; using cleaned idea-card text."
        )
    except Exception as exc:
        warnings.append(f"Condense step failed ({exc}); using cleaned idea-card text.")
    return cleaned_query[:3500], warnings


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
        from vs_app.modules.rag.augmentation.candidate_merger import (
            CandidateWindowPolicy,
            merge_candidate_sources,
        )
        from vs_app.modules.rag.fingerprints import build_rag_debug_fingerprints
        from vs_app.modules.rag.config.runtime import derive_rag_runtime_config

        try:
            total_started = perf_counter()
            timing_ms: dict[str, int] = {}
            runtime_config = derive_rag_runtime_config(request.final_output_count)
            semantic_fetch_k = min(max(1, runtime_config.semantic_fetch_k), 50)
            historical_ticket_fetch_k = min(max(1, runtime_config.historical_ticket_fetch_k), 40)
            faiss_dir = str(_FAISS_DIR)

            # Step 1: Extract
            yield _sse("step", {"step": "extract", "label": f"Reading {request.ticket_id or 'idea card'}..."})
            step_started = perf_counter()
            if request.idea_card_text:
                raw_text = request.idea_card_text
            elif request.ticket_id:
                from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text
                raw_text = await asyncio.to_thread(
                    extract_idea_card_text,
                    doc_id=request.ticket_id,
                    local_card_dir=_IDEA_CARDS_DIR,
                )
            else:
                raise ValueError("No idea card text or ticket ID provided")
            timing_ms["extract"] = round((perf_counter() - step_started) * 1000)

            # Step 2: Condense once. The same condensed query feeds semantic VS and historical retrieval.
            yield _sse("step", {"step": "condense", "label": "Condensing idea card for retrieval..."})
            step_started = perf_counter()
            cleaned_query = await asyncio.to_thread(clean_ppt_text, raw_text)
            query_for_prompt, warnings = await _condense_or_fallback(
                condense_idea_card,
                raw_text,
                cleaned_query,
            )
            retrieval_query = query_for_prompt or cleaned_query
            timing_ms["condense"] = round((perf_counter() - step_started) * 1000)

            # Step 3: Semantic VS + historical FAISS both use the condensed query.
            yield _sse("step", {"step": "semantic", "label": "Searching VS and history from condensed query..."})
            step_started = perf_counter()
            exclude_ids = _source_ticket_exclusions(request)
            historical_kwargs = {
                "historical_faiss_dir": faiss_dir,
                "historical_search_backend": _HISTORICAL_BACKEND,
                "historical_azure_index_name": _HISTORICAL_AZURE_INDEX,
                "max_ticket_hits": historical_ticket_fetch_k,
            }
            if "exclude_ticket_ids" in inspect.signature(retrieve_historical_support).parameters:
                historical_kwargs["exclude_ticket_ids"] = exclude_ids
            semantic_task = asyncio.create_task(
                asyncio.to_thread(
                    retrieve_semantic_candidates,
                    retrieval_query,
                    top_k=semantic_fetch_k,
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
            timing_ms["retrieval"] = round((perf_counter() - step_started) * 1000)

            # Step 4: Merge candidates
            yield _sse("step", {"step": "merge", "label": "Merging semantic and historical candidates..."})
            step_started = perf_counter()
            augmented = merge_candidate_sources(
                semantic_candidates,
                historical.get("historical_value_stream_support", []),
                policy=CandidateWindowPolicy(
                    max_semantic_plus_historical=runtime_config.max_semantic_plus_historical,
                    max_semantic_only=runtime_config.max_semantic_only,
                    max_historical_only=runtime_config.max_historical_only,
                    max_supporting_tickets_per_candidate=runtime_config.max_supporting_tickets_per_candidate,
                ),
                max_llm_candidates=runtime_config.llm_candidate_window,
            )
            timing_ms["merge"] = round((perf_counter() - step_started) * 1000)

            # Step 5: One global review-pool LLM selection.
            yield _sse("step", {"step": "llm_select", "label": "Running review-pool LLM selection..."})
            step_started = perf_counter()
            from vs_app.modules.rag.augmentation.finalizer import generate_review_pool_value_streams
            generated = await asyncio.to_thread(
                generate_review_pool_value_streams,
                query_for_prompt=retrieval_query,
                llm_candidates=augmented["llm_candidates"],
                final_output_count=runtime_config.final_output_count,
                prompt_budget={
                    "idea_card_prompt_chars": runtime_config.idea_card_prompt_chars,
                    "candidate_description_chars": runtime_config.candidate_description_chars,
                    "analogs_per_candidate": runtime_config.analogs_per_candidate,
                    "analog_chars": runtime_config.analog_chars,
                    "historical_ticket_ids_per_candidate": runtime_config.historical_ticket_ids_per_candidate,
                },
            )
            timing_ms["final_llm"] = round((perf_counter() - step_started) * 1000)
            timing_ms["total"] = round((perf_counter() - total_started) * 1000)
            raw_response = generated["raw_response"]
            review_pool_llm_output = (
                raw_response.get("single_review_pool_pass")
                if isinstance(raw_response, dict)
                else None
            )
            logger.info(
                "[RAG] review_pool=%s llm_candidates=%s prompt_chars=%s timing_ms=%s",
                runtime_config.final_output_count,
                len(generated.get("candidates_used", [])),
                raw_response.get("prompt_debug", {}).get("prompt_chars")
                if isinstance(raw_response, dict)
                else None,
                timing_ms,
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
                "candidate_window_policy": augmented.get("candidate_window_policy", {}),
                "candidate_window_counts": augmented.get("candidate_window_counts", {}),
                "rag_runtime_config": asdict(runtime_config),
                "historical_source": historical.get("historical_source", ""),
                "raw_response": raw_response,
                "review_pool_llm_output": review_pool_llm_output,
                "direct_llm_output": review_pool_llm_output,
                "historical_llm_output": None,
                "query_preparation": {
                    "cleaned_query": cleaned_query,
                    "query_for_prompt": query_for_prompt,
                },
                "warnings": warnings,
                "evidence": historical.get("historical_value_stream_support", []),
                "debug": {
                    **debug,
                    "timing_ms": timing_ms,
                    "prompt_debug": generated.get("raw_response", {}).get("prompt_debug", {}),
                    "rag_runtime_config": asdict(runtime_config),
                    "candidate_window_counts": augmented.get("candidate_window_counts", {}),
                },
                "historical_excluded_ticket_ids": exclude_ids or [],
                "ground_truth": _ground_truth_for_ticket(request.ticket_id),
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
        semantic_fetch_k=request.semantic_fetch_k,
        historical_ticket_fetch_k=request.historical_ticket_fetch_k,
        llm_candidate_window=request.llm_candidate_window,
        final_output_count=request.final_output_count,
        historical_search_backend=_HISTORICAL_BACKEND,
        historical_azure_index_name=_HISTORICAL_AZURE_INDEX,
        use_llm_finalizer=request.use_llm_finalizer,
        exclude_source_ticket_from_historical=request.exclude_source_ticket_from_historical,
    )
    result = await container.rag.analyze(command)
    response = ValueStreamRagResponse.from_result(result)
    if request.ticket_id:
        response.ground_truth = _ground_truth_for_ticket(request.ticket_id)
    return response
