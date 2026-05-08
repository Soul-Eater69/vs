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

    llm_candidate_window = min(28, max(requested + 5, math.ceil(requested * 1.25)))
    semantic_fetch_k = min(50, max(25, requested * 2))
    historical_ticket_fetch_k = min(30, max(15, math.ceil(requested * 1.25)))

    max_semantic_plus_historical = min(12, max(6, math.ceil(llm_candidate_window * 0.42)))
    max_historical_only = min(8, max(4, math.floor(llm_candidate_window * 0.28)))
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
