from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from typing import Any, Dict, List, Optional


def select_value_streams(
    query: str,
    *,
    fetch_count: int = 12,
    semantic_fetch_k: int = 40,
    historical_ticket_fetch_k: int = 35,
    llm_candidate_window: int = 30,
    final_output_count: int | None = None,
    historical_faiss_dir: str = "ticket_data/_faiss",
    historical_search_backend: str | None = None,
    historical_azure_index_name: str | None = None,
    allowed_value_stream_names: Optional[List[str]] = None,
    exclude_ticket_ids: Optional[List[str]] = None,
) -> dict:
    from .augmentation.candidate_merger import CandidateWindowPolicy, merge_candidate_sources
    from .augmentation.finalizer import generate_value_streams
    from .fingerprints import build_rag_debug_fingerprints
    from .query.views import clean_ppt_text, condense_idea_card
    from .retrieval.historical_retriever import filter_historical_result, retrieve_historical_support
    from .retrieval.semantic_retriever import retrieve_semantic_candidates

    top_k = min(max(1, semantic_fetch_k), 50)
    max_ticket_hits = min(max(1, historical_ticket_fetch_k), 40)
    cleaned_query = clean_ppt_text(query)
    query_for_prompt = condense_idea_card(query, max_chars=3500)
    retrieval_query = query_for_prompt or cleaned_query

    with ThreadPoolExecutor(max_workers=2) as executor:
        semantic_future = executor.submit(
            retrieve_semantic_candidates,
            retrieval_query,
            top_k=top_k,
            allowed_value_stream_names=allowed_value_stream_names,
        )
        historical_future = executor.submit(
            _retrieve_historical_support_compat,
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

    augmented = merge_candidate_sources(
        semantic_candidates,
        historical.get("historical_value_stream_support", []),
        policy=CandidateWindowPolicy(),
        max_llm_candidates=llm_candidate_window,
    )
    generated = generate_value_streams(
        query_for_prompt=retrieval_query,
        llm_candidates=augmented["llm_candidates"],
        auto_selected=augmented["auto_selected_value_streams"],
        historical_ticket_hits=historical.get("historical_ticket_hits", []),
    )
    _apply_final_output_count(generated, final_output_count)
    raw_response = generated["raw_response"]
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
    return {
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
        "historical_source": historical.get("historical_source", ""),
        "raw_response": raw_response,
        "direct_llm_output": (
            raw_response.get("direct_pass") if isinstance(raw_response, dict) else None
        ),
        "historical_llm_output": (
            raw_response.get("historical_gap_pass") if isinstance(raw_response, dict) else None
        ),
        "query_preparation": {
            "cleaned_query": cleaned_query,
            "query_for_prompt": query_for_prompt,
        },
        "warnings": [],
        "debug": debug,
        "historical_excluded_ticket_ids": list(exclude_ticket_ids or []),
    }


def _retrieve_historical_support_compat(
    retrieve_historical_support,
    query: str,
    *,
    historical_faiss_dir: str,
    historical_search_backend: str | None = None,
    historical_azure_index_name: str | None = None,
    max_ticket_hits: int,
    exclude_ticket_ids: Optional[List[str]] = None,
) -> dict:
    kwargs = {
        "historical_faiss_dir": historical_faiss_dir,
        "max_ticket_hits": max_ticket_hits,
    }
    if "historical_search_backend" in inspect.signature(retrieve_historical_support).parameters:
        kwargs["historical_search_backend"] = historical_search_backend
    if "historical_azure_index_name" in inspect.signature(retrieve_historical_support).parameters:
        kwargs["historical_azure_index_name"] = historical_azure_index_name
    if "exclude_ticket_ids" in inspect.signature(retrieve_historical_support).parameters:
        kwargs["exclude_ticket_ids"] = exclude_ticket_ids
    return retrieve_historical_support(query, **kwargs)


def _apply_final_output_count(generated: dict, final_output_count: int | None) -> None:
    if final_output_count is None:
        return
    limit = max(0, int(final_output_count or 0))
    generated["selected_value_streams"] = list(generated.get("selected_value_streams") or [])[:limit]
    raw_response = generated.get("raw_response")
    if isinstance(raw_response, dict):
        raw_response["selected_value_streams"] = list(raw_response.get("selected_value_streams") or [])[:limit]


def run_historical_rag_pipeline(
    ppt_text: str,
    *,
    allowed_value_stream_names: Optional[List[str]] = None,
    fetch_count: int = 12,
    historical_faiss_dir: str = "ticket_data/_faiss",
    historical_search_backend: str | None = None,
    historical_azure_index_name: str | None = None,
) -> Dict[str, Any]:
    result = select_value_streams(
        ppt_text,
        fetch_count=fetch_count,
        historical_faiss_dir=historical_faiss_dir,
        historical_search_backend=historical_search_backend,
        historical_azure_index_name=historical_azure_index_name,
        allowed_value_stream_names=allowed_value_stream_names,
    )
    return {
        "selected_value_streams": result.get("selected_value_streams", []),
        "auto_selected_value_streams": result.get("auto_selected_value_streams", []),
        "llm_selected_value_streams": result.get("llm_selected_value_streams", []),
        "rescued_confirmed_merged_value_streams": result.get(
            "rescued_confirmed_merged_value_streams",
            [],
        ),
        "rescued_historical_gap_fill_value_streams": result.get(
            "rescued_historical_gap_fill_value_streams",
            [],
        ),
        "dropped_historical_gap_fill_value_streams": result.get(
            "dropped_historical_gap_fill_value_streams",
            [],
        ),
        "rejected_candidates": result.get("rejected_candidates", []),
        "semantic_candidate_value_streams": result.get("semantic_candidate_value_streams", []),
        "historical_candidate_value_streams": result.get("historical_candidate_value_streams", []),
        "merged_candidate_value_streams": result.get("merged_candidate_value_streams", []),
        "historical_ticket_hits": result.get("historical_ticket_hits", []),
        "historical_value_stream_support": result.get("historical_value_stream_support", []),
        "candidate_value_streams": result.get("candidate_value_streams", []),
        "llm_candidates": result.get("llm_candidates", []),
        "historical_source": result.get("historical_source", ""),
        "raw_response": result.get("raw_response"),
        "query_preparation": result.get("query_preparation", {}),
        "warnings": result.get("warnings", []),
        "debug": result.get("debug", {}),
        "historical_excluded_ticket_ids": result.get("historical_excluded_ticket_ids", []),
    }
