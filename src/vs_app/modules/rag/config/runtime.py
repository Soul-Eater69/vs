from __future__ import annotations

from dataclasses import dataclass
import math


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


def derive_rag_runtime_config(final_output_count: int | None) -> RagRuntimeConfig:
    requested = max(1, int(final_output_count or 12))

    # Wider LLM candidate window so strong candidates aren't cut at the lane caps
    # before the LLM ever sees them. Slim output schema means input-side growth has
    # only modest latency cost.
    llm_candidate_window = min(40, max(25, math.ceil(requested * 1.75)))
    semantic_fetch_k = min(60, max(30, math.ceil(requested * 2.5)))
    # Historical fetch_k matters for sparse-tag value streams (Issue Payment, Manage
    # Invoice and Payment Receipt, etc.) — they only appear in a small fraction of
    # historical tickets, so we need a wide net to surface 2-3 evidence hits.
    historical_ticket_fetch_k = min(60, max(35, math.ceil(requested * 2.5)))

    # Most true positives live in the merged (semantic+historical) lane, so we
    # protect that lane first. Semantic-only is the riskiest lane (this is where
    # most false positives came from in evaluation), so we keep it small.
    max_semantic_plus_historical = min(
        llm_candidate_window,
        max(requested + 6, math.ceil(llm_candidate_window * 0.75)),
    )
    max_historical_only = min(
        5,
        max(2, math.floor(llm_candidate_window * 0.15)),
    )
    max_semantic_only = max(
        0,
        llm_candidate_window - max_semantic_plus_historical - max_historical_only,
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
        idea_card_prompt_chars=1800,
        candidate_description_chars=100,
        analogs_per_candidate=2,
        analog_chars=80,
        historical_ticket_ids_per_candidate=2,
    )
