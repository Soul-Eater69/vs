"""Plain value-stream selection pipeline.

Flow: PPT text -> semantic vector search + historical evidence -> LLM selection.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional

from clients.azure_direct_client import AzureDirectSearchClient
from clients.generation_service import GenerationService
from text import clean_ppt_text, condense_idea_card, normalize_for_search
from prompts import build_plain_selection_prompt, build_selection_system_prompt, SelectionResult
from pipelines.retrieval.pipeline import (
    build_context,
    build_historical_vs_evidence,
    load_ticket_vs_maps,
    retrieve_historical_chunks,
)

logger = logging.getLogger(__name__)








def select_value_streams(query: str, fetch_count: int = 12) -> dict:
    """
    Run the plain pipeline:
    1. Semantic hybrid retrieval against value-stream index
    2. Limit candidates
    3. LLM selection
    """
    client = AzureDirectSearchClient()
    gen_svc = GenerationService()

    top_k = min(max(12, fetch_count), 20)
    
    try:
        results = client.search_hybrid(
            query,
            top_k=top_k,
            use_semantic_rerank=True,
            filter_expression="node_type eq 'ValueStream'",
        )
    except Exception as exc:
        logger.warning("Hybrid semantic reranking unavailable, falling back to vector search: %s", exc)
        results = client.search_vector(query, top_k=top_k, filter_expression="node_type eq 'ValueStream'")

    if not results:
        return {
            "selected_value_streams": [],
            "rejected_candidates": [],
            "raw_response": None,
            "candidates_used": [],
        }

    candidate_pool: List[dict] = list(results)[:top_k]
    context = build_context(candidate_pool)
    # Condense the raw PPT text via a fast LLM call — strips slide noise,
    # repeated headers, metadata while preserving business essence and keywords.
    query_for_prompt = condense_idea_card(query, max_chars=3500)
    prompt = build_plain_selection_prompt(query=query_for_prompt, context=context)
    result = gen_svc.generate_structured(
        query=prompt,
        output_schema=SelectionResult,
        system_prompt=build_selection_system_prompt(),
    )
    parsed = result.model_dump() if hasattr(result, "model_dump") else {"selected_value_streams": []}

    return {
        "selected_value_streams": parsed.get("selected_value_streams", []),
        "raw_response": parsed,
        "candidates_used": [
            {
                "entity_id": m.get("entity_id", ""),
                "entity_name": m.get("entity_name", ""),
                "score": (
                    m.get("@search.reranker_score")
                    if m.get("@search.reranker_score") is not None
                    else m.get("@search.score", 0)
                ) or 0,
                "@search.score": m.get("@search.score", 0),
                "@search.reranker_score": m.get("@search.reranker_score", 0),
                "_support_count": m.get("_support_count", 1),
                "_aggregated_best_score": m.get("_aggregated_best_score", 0),
            }
            for m in candidate_pool
        ],
    }
