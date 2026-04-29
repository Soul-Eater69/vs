from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from typing import Any, Dict, List, Optional


def select_value_streams(
    query: str,
    *,
    fetch_count: int = 12,
    historical_faiss_dir: str = "ticket_data/_faiss",
    allowed_value_stream_names: Optional[List[str]] = None,
    exclude_ticket_ids: Optional[List[str]] = None,
) -> dict:
    from .augmentation.candidate_merger import merge_candidate_sources
    from .augmentation.finalizer import generate_value_streams
    from .fingerprints import build_rag_debug_fingerprints
    from .query.views import clean_ppt_text, condense_idea_card
    from .retrieval.historical_retriever import retrieve_historical_support
    from .retrieval.semantic_retriever import retrieve_semantic_candidates

    top_k = min(max(12, fetch_count), 50)
    max_llm_candidates = min(max(top_k, 18), 36)
    cleaned_query = clean_ppt_text(query)

    with ThreadPoolExecutor(max_workers=2) as executor:
        condense_future = executor.submit(condense_idea_card, query, max_chars=3500)
        semantic_future = executor.submit(
            retrieve_semantic_candidates,
            cleaned_query,
            top_k=top_k,
            allowed_value_stream_names=allowed_value_stream_names,
        )
        query_for_prompt = condense_future.result()
        semantic_candidates = semantic_future.result()

    historical = _retrieve_historical_support_compat(
        retrieve_historical_support,
        query_for_prompt or cleaned_query,
        historical_faiss_dir=historical_faiss_dir,
        max_ticket_hits=min(max(12, fetch_count), 24),
        exclude_ticket_ids=exclude_ticket_ids,
    )

    augmented = merge_candidate_sources(
        semantic_candidates,
        historical.get("historical_value_stream_support", []),
        max_llm_candidates=max_llm_candidates,
    )
    generated = generate_value_streams(
        query_for_prompt=query_for_prompt or cleaned_query,
        llm_candidates=augmented["llm_candidates"],
        auto_selected=augmented["auto_selected_value_streams"],
        historical_ticket_hits=historical.get("historical_ticket_hits", []),
    )
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
    }


def _retrieve_historical_support_compat(
    retrieve_historical_support,
    query: str,
    *,
    historical_faiss_dir: str,
    max_ticket_hits: int,
    exclude_ticket_ids: Optional[List[str]] = None,
) -> dict:
    kwargs = {
        "historical_faiss_dir": historical_faiss_dir,
        "max_ticket_hits": max_ticket_hits,
    }
    if "exclude_ticket_ids" in inspect.signature(retrieve_historical_support).parameters:
        kwargs["exclude_ticket_ids"] = exclude_ticket_ids
    return retrieve_historical_support(query, **kwargs)


def run_historical_rag_pipeline(
    ppt_text: str,
    *,
    allowed_value_stream_names: Optional[List[str]] = None,
    fetch_count: int = 12,
    historical_faiss_dir: str = "ticket_data/_faiss",
) -> Dict[str, Any]:
    result = select_value_streams(
        ppt_text,
        fetch_count=fetch_count,
        historical_faiss_dir=historical_faiss_dir,
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
    }
