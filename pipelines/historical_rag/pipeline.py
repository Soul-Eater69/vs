from __future__ import annotations

from typing import List, Optional

from pipelines.historical_rag.augmentation import merge_candidate_sources
from pipelines.historical_rag.generation import generate_value_streams
from pipelines.historical_rag.retrieval import (
    retrieve_historical_support,
    retrieve_semantic_candidates,
)


def select_value_streams(
    query: str,
    *,
    fetch_count: int = 12,
    historical_summary_dir: str = "ticket_data",
    historical_faiss_dir: str = "ticket_data/_faiss",
    local_vs_map_dir: str = "ticket_chunks",
    historical_backend: str = "auto",
    allowed_value_stream_names: Optional[List[str]] = None,
) -> dict:
    top_k = min(max(12, fetch_count), 24)

    semantic_candidates = retrieve_semantic_candidates(
        query,
        top_k=top_k,
        allowed_value_stream_names=allowed_value_stream_names,
    )
    historical = retrieve_historical_support(
        query,
        historical_summary_dir=historical_summary_dir,
        historical_faiss_dir=historical_faiss_dir,
        local_vs_map_dir=local_vs_map_dir,
        historical_backend=historical_backend,
        top_per_view=top_k,
        max_historical_chunks=60,
        max_ticket_hits=12,
    )

    augmented = merge_candidate_sources(
        semantic_candidates,
        historical.get("historical_value_stream_support", []),
        max_llm_candidates=max(top_k, 14),
    )
    generated = generate_value_streams(
        query,
        augmented["llm_candidates"],
        auto_selected=augmented["auto_selected_value_streams"],
    )
    return {
        "selected_value_streams": generated["selected_value_streams"],
        "auto_selected_value_streams": augmented["auto_selected_value_streams"],
        "llm_selected_value_streams": generated["llm_selected_value_streams"],
        "historical_ticket_hits": historical.get("historical_ticket_hits", []),
        "historical_value_stream_support": historical.get("historical_value_stream_support", []),
        "candidate_value_streams": augmented["merged_candidates"],
        "llm_candidates": generated["candidates_used"],
        "historical_source": historical.get("historical_source", ""),
        "raw_response": generated["raw_response"],
    }
