from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import logging
from time import perf_counter
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)


def select_value_streams(
    query: str,
    *,
    semantic_fetch_k: int = 40,
    historical_ticket_fetch_k: int = 35,
    llm_candidate_window: int = 30,
    final_output_count: int | None = None,
    historical_faiss_dir: str = "ticket_data/_faiss",
    historical_search_backend: str | None = "azure",
    historical_azure_index_name: str | None = None,
    exclude_ticket_ids: Optional[List[str]] = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict:
    from .augmentation.candidate_merger import CandidateWindowPolicy, merge_candidate_sources
    from .augmentation.finalizer import generate_review_pool_value_streams
    from .config.runtime import derive_rag_runtime_config
    from .fingerprints import build_rag_debug_fingerprints
    from .query.views import prepare_idea_card_for_rag
    from .retrieval.historical_retriever import filter_historical_result, retrieve_historical_support
    from .retrieval.semantic_retriever import retrieve_semantic_candidates

    runtime_config = derive_rag_runtime_config(final_output_count)
    runtime_config_dict = asdict(runtime_config)
    if final_output_count is not None:
        semantic_fetch_k = runtime_config.semantic_fetch_k
        historical_ticket_fetch_k = runtime_config.historical_ticket_fetch_k
        llm_candidate_window = runtime_config.llm_candidate_window

    top_k = min(max(1, semantic_fetch_k), 50)
    max_ticket_hits = min(max(1, historical_ticket_fetch_k), 40)
    _emit_progress(progress_callback, "condense", "Condensing idea card...")
    prepared = prepare_idea_card_for_rag(query, max_chars=3500)
    cleaned_query = prepared.cleaned_text
    query_for_prompt = prepared.condensed_text
    retrieval_query = query_for_prompt or cleaned_query

    with ThreadPoolExecutor(max_workers=2) as executor:
        _emit_progress(progress_callback, "semantic", "Searching value-stream catalog...")
        semantic_future = executor.submit(
            retrieve_semantic_candidates,
            retrieval_query,
            top_k=top_k,
        )
        _emit_progress(progress_callback, "historical", "Searching historical analogs...")
        historical_future = executor.submit(
            retrieve_historical_support,
            retrieval_query,
            historical_faiss_dir=historical_faiss_dir,
            historical_search_backend=historical_search_backend,
            historical_azure_index_name=historical_azure_index_name,
            max_ticket_hits=max_ticket_hits,
            exclude_ticket_ids=exclude_ticket_ids,
        )
        semantic_candidates = semantic_future.result()
        historical = historical_future.result()
    historical = filter_historical_result(historical, exclude_ticket_ids)

    _emit_progress(progress_callback, "merge", "Merging semantic and historical evidence...")
    augmented = merge_candidate_sources(
        semantic_candidates,
        historical.get("historical_value_stream_support", []),
        policy=CandidateWindowPolicy(
            max_semantic_plus_historical=runtime_config.max_semantic_plus_historical,
            max_semantic_only=runtime_config.max_semantic_only,
            max_historical_only=runtime_config.max_historical_only,
            max_supporting_tickets_per_candidate=runtime_config.max_supporting_tickets_per_candidate,
        )
        if final_output_count is not None
        else CandidateWindowPolicy(),
        max_llm_candidates=llm_candidate_window,
    )
    finalizer_started = perf_counter()
    _emit_progress(progress_callback, "llm_select", "Running Review Pool LLM selection...")
    generated = generate_review_pool_value_streams(
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
    raw_response = generated["raw_response"]
    review_pool_llm_output = (
        raw_response.get("single_review_pool_pass") if isinstance(raw_response, dict) else None
    )
    logger.info(
        "[RAG] review_pool=%s llm_candidates=%s prompt_chars=%s finalizer_ms=%s",
        runtime_config.final_output_count,
        len(generated.get("candidates_used", [])),
        raw_response.get("prompt_debug", {}).get("prompt_chars") if isinstance(raw_response, dict) else None,
        round((perf_counter() - finalizer_started) * 1000),
    )
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
    debug["rag_runtime_config"] = runtime_config_dict
    debug["prompt_debug"] = raw_response.get("prompt_debug", {}) if isinstance(raw_response, dict) else {}
    debug["candidate_window_counts"] = augmented.get("candidate_window_counts", {})
    _emit_progress(progress_callback, "finalize", "Finalizing selections...")
    return {
        "selected_value_streams": generated["selected_value_streams"],
        "auto_selected_value_streams": augmented["auto_selected_value_streams"],
        "llm_selected_value_streams": generated["llm_selected_value_streams"],
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
        "rag_runtime_config": runtime_config_dict,
        "historical_source": historical.get("historical_source", ""),
        "raw_response": raw_response,
        "review_pool_llm_output": review_pool_llm_output,
        "query_preparation": {
            "source_ticket_title": prepared.source_ticket_title,
            "cleaned_query": cleaned_query,
            "query_for_prompt": query_for_prompt,
        },
        "warnings": [],
        "debug": debug,
        "historical_excluded_ticket_ids": list(exclude_ticket_ids or []),
    }


def _emit_progress(
    progress_callback: Callable[[str, str], None] | None,
    step: str,
    label: str,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(step, label)
    except Exception:
        logger.debug("RAG progress callback failed", exc_info=True)
