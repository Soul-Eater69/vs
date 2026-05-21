from __future__ import annotations

from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class RagRuntimeConfig:
    final_output_count: int
    semantic_fetch_k: int
    historical_ticket_fetch_k: int
    llm_candidate_window: int
    max_semantic_plus_historical: int
    max_semantic_only: int
    max_historical_only: int
    max_supporting_tickets_per_candidate: int
    idea_card_prompt_chars: int
    candidate_description_chars: int
    analogs_per_candidate: int
    analog_chars: int
    historical_ticket_ids_per_candidate: int


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 100) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, min(max_value, value))


def derive_rag_runtime_config(final_output_count: int | None) -> RagRuntimeConfig:
    requested = max(1, int(final_output_count or 12))

    # Fixed broad retrieval. Retrieval is cheaper than the LLM call, so we don't
    # shrink it with the requested output count — the user's slider only affects
    # the *output* count, not how widely we search for evidence.
    semantic_fetch_k = 60
    historical_ticket_fetch_k = _env_int(
        "RAG_HISTORICAL_TICKET_FETCH_K",
        60,
        min_value=1,
        max_value=100,
    )

    # Adaptive LLM candidate window. This is how many evidence-qualified candidates
    # the LLM may inspect, not how many it returns. Bigger than `requested` so the
    # model has room to reject weak picks.
    llm_candidate_window = min(50, max(35, math.ceil(requested * 3.0)))

    # Upper bounds for evidence-qualified selection in candidate_merger.py.
    # Merged lane has no hard quota — it can fill the whole window if enough
    # qualified candidates exist. Historical-only and semantic-only are
    # cap-limited so they can't crowd out merged candidates.
    max_semantic_plus_historical = llm_candidate_window
    max_historical_only = min(8, max(4, math.floor(llm_candidate_window * 0.16)))
    max_semantic_only = min(
        5,
        max(1, llm_candidate_window - max_semantic_plus_historical - max_historical_only),
    )
    idea_card_prompt_chars = _env_int(
        "RAG_IDEA_CARD_PROMPT_CHARS",
        1800,
        min_value=500,
        max_value=6000,
    )
    candidate_description_chars = _env_int(
        "RAG_CANDIDATE_DESCRIPTION_CHARS",
        100,
        min_value=50,
        max_value=500,
    )
    analogs_per_candidate = _env_int(
        "RAG_ANALOGS_PER_CANDIDATE",
        2,
        min_value=0,
        max_value=5,
    )
    analog_chars = _env_int(
        "RAG_ANALOG_CHARS",
        80,
        min_value=40,
        max_value=400,
    )
    historical_ticket_ids_per_candidate = _env_int(
        "RAG_HISTORICAL_TICKET_IDS_PER_CANDIDATE",
        2,
        min_value=0,
        max_value=5,
    )

    return RagRuntimeConfig(
        final_output_count=requested,
        semantic_fetch_k=semantic_fetch_k,
        historical_ticket_fetch_k=historical_ticket_fetch_k,
        llm_candidate_window=llm_candidate_window,
        max_semantic_plus_historical=max_semantic_plus_historical,
        max_semantic_only=max_semantic_only,
        max_historical_only=max_historical_only,
        max_supporting_tickets_per_candidate=2,
        idea_card_prompt_chars=idea_card_prompt_chars,
        candidate_description_chars=candidate_description_chars,
        analogs_per_candidate=analogs_per_candidate,
        analog_chars=analog_chars,
        historical_ticket_ids_per_candidate=historical_ticket_ids_per_candidate,
    )
