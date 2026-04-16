from __future__ import annotations

from typing import Any, Dict, List, Optional

from pipelines.historical_rag.augmentation import merge_candidate_sources
from pipelines.historical_rag.generation import generate_value_streams
from pipelines.historical_rag.retrieval import (
    retrieve_historical_support,
    retrieve_semantic_candidates,
)
from text import clean_ppt_text, condense_idea_card


def select_value_streams(
    query: str,
    *,
    fetch_count: int = 12,
    historical_faiss_dir: str = "ticket_data/_faiss",
    allowed_value_stream_names: Optional[List[str]] = None,
) -> dict:
    top_k = min(max(12, fetch_count), 24)
    cleaned_query = clean_ppt_text(query)
    query_for_prompt = condense_idea_card(query, max_chars=3500)

    semantic_candidates = retrieve_semantic_candidates(
        cleaned_query,
        top_k=top_k,
        allowed_value_stream_names=allowed_value_stream_names,
    )
    historical = retrieve_historical_support(
        query_for_prompt or cleaned_query,
        historical_faiss_dir=historical_faiss_dir,
        max_ticket_hits=12,
    )

    augmented = merge_candidate_sources(
        semantic_candidates,
        historical.get("historical_value_stream_support", []),
        max_llm_candidates=max(top_k, 14),
    )
    generated = generate_value_streams(
        query_for_prompt=query_for_prompt or cleaned_query,
        llm_candidates=augmented["llm_candidates"],
        auto_selected=augmented["auto_selected_value_streams"],
    )
    return {
        "selected_value_streams": generated["selected_value_streams"],
        "auto_selected_value_streams": augmented["auto_selected_value_streams"],
        "llm_selected_value_streams": generated["llm_selected_value_streams"],
        "rejected_candidates": [],
        "historical_ticket_hits": historical.get("historical_ticket_hits", []),
        "historical_value_stream_support": historical.get("historical_value_stream_support", []),
        "candidate_value_streams": augmented["merged_candidates"],
        "llm_candidates": generated["candidates_used"],
        "historical_source": historical.get("historical_source", ""),
        "raw_response": generated["raw_response"],
        "query_preparation": {
            "cleaned_query": cleaned_query,
            "query_for_prompt": query_for_prompt,
        },
        "warnings": [],
    }


def run_historical_rag_pipeline(
    ppt_text: str,
    *,
    allowed_value_stream_names: Optional[List[str]] = None,
    fetch_count: int = 12,
    historical_faiss_dir: str = "ticket_data/_faiss",
) -> Dict[str, Any]:
    """
    Public entrypoint for the merged semantic + historical retrieval pipeline.

    This keeps the same role as the other pipeline entrypoints:
      - accept raw idea-card text
      - orchestrate retrieval, augmentation, and generation
      - return a structured result dict
    """
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
        "rejected_candidates": result.get("rejected_candidates", []),
        "historical_ticket_hits": result.get("historical_ticket_hits", []),
        "historical_value_stream_support": result.get("historical_value_stream_support", []),
        "candidate_value_streams": result.get("candidate_value_streams", []),
        "llm_candidates": result.get("llm_candidates", []),
        "historical_source": result.get("historical_source", ""),
        "raw_response": result.get("raw_response"),
        "query_preparation": result.get("query_preparation", {}),
        "warnings": result.get("warnings", []),
    }
