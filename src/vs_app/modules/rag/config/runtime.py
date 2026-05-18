from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RagRuntimeConfig:
    final_output_count: int
    semantic_fetch_k: int
    historical_ticket_fetch_k: int
    historical_evidence_top_k: int
    min_historical_evidence_score: float
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

    # Fixed broad retrieval. Retrieval is cheaper than the LLM call, so we don't
    # shrink it with the requested output count; the user's slider only affects
    # the *output* count, not how widely we search for evidence.
    semantic_fetch_k = 60

    # Number of past IDMT/Jira tickets to retrieve as historical analog hits.
    # This is NOT the number of historical value-stream candidates.
    historical_ticket_fetch_k = 6

    # Same as historical_ticket_fetch_k for now. Controls retrieved ticket hits only.
    # Qualified hits may produce more than 6 value-stream candidates.
    historical_evidence_top_k = 6

    # Azure hybrid scores are not absolute similarity probabilities. Use all
    # top-k historical ticket hits as candidate-expansion priors.
    min_historical_evidence_score = 0.0

    # Adaptive LLM candidate window. This is how many evidence-qualified candidates
    # the LLM may inspect, not how many it returns. Bigger than `requested` so the
    # model has room to reject weak picks.
    llm_candidate_window = min(50, max(35, math.ceil(requested * 3.0)))

    # Recall-friendly lane caps for value-stream candidates. The merger can reuse
    # unused lane capacity, so semantic-only still fills the window when history is absent.
    max_semantic_plus_historical = llm_candidate_window
    max_historical_only = min(8, max(4, math.floor(llm_candidate_window * 0.16)))
    max_semantic_only = 5

    return RagRuntimeConfig(
        final_output_count=requested,
        semantic_fetch_k=semantic_fetch_k,
        historical_ticket_fetch_k=historical_ticket_fetch_k,
        historical_evidence_top_k=historical_evidence_top_k,
        min_historical_evidence_score=min_historical_evidence_score,
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
