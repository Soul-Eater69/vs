from __future__ import annotations

import logging
import re
from typing import List

from .prompt_context import (
    build_review_pool_candidate_prompt,
    build_review_pool_system_prompt,
)

logger = logging.getLogger(__name__)


def generate_review_pool_value_streams(
    query_for_prompt: str,
    llm_candidates: List[dict],
    final_output_count: int,
    prompt_budget: dict | None = None,
) -> dict:
    from time import perf_counter

    from vs_app.integrations.clients.generation_service import GenerationService
    from vs_app.modules.prompts.schemas.value_stream import ReviewPoolPickResult

    started = perf_counter()
    requested = max(1, int(final_output_count or 12))
    candidates = list(llm_candidates or [])
    max_select = min(requested, len(candidates))

    if not candidates:
        return {
            "selected_value_streams": [],
            "llm_selected_value_streams": [],
            "raw_response": {
                "selected_value_streams": [],
                "requested_final_output_count": requested,
                "selection_budget": {"max_select": 0, "candidate_count": 0},
                "single_review_pool_pass": _empty_pass(),
                "missed_strong_candidates": [],
                "prompt_debug": {
                    "prompt_chars": 0,
                    "system_prompt_chars": 0,
                    "candidate_count": 0,
                    "idea_card_chars": len(query_for_prompt or ""),
                },
                "timing_ms": {"final_llm": 0, "total": 0},
            },
            "candidates_used": [],
        }

    prompt = build_review_pool_candidate_prompt(
        query_for_prompt=query_for_prompt,
        candidates=candidates,
        final_output_count=requested,
        prompt_budget=prompt_budget or {},
    )
    system_prompt = build_review_pool_system_prompt(max_select=max_select)

    import os as _os
    review_pool_effort = _os.environ.get("REVIEW_POOL_REASONING_EFFORT", "low")
    llm_started = perf_counter()
    parsed: dict = {"picks": []}
    llm_error: str | None = None
    try:
        result = GenerationService().generate_structured(
            query=prompt,
            output_schema=ReviewPoolPickResult,
            system_prompt=system_prompt,
            reasoning_effort=review_pool_effort,
        )
        parsed = result.model_dump() if hasattr(result, "model_dump") else {"picks": []}
    except Exception as exc:
        llm_error = f"{type(exc).__name__}: {exc}"
        logger.exception("[RAG] review_pool_llm structured call failed; falling back to backfill")
    llm_elapsed_ms = round((perf_counter() - llm_started) * 1000)
    logger.info(
        "[RAG] review_pool_llm: candidates=%d prompt_chars=%d system_chars=%d effort=%s elapsed_ms=%d picks=%d error=%s",
        len(candidates),
        len(prompt),
        len(system_prompt),
        review_pool_effort,
        llm_elapsed_ms,
        len(parsed.get("picks") or []),
        llm_error or "none",
    )
    candidates_by_id = {
        str(candidate.get("entity_id") or "").strip().lower(): candidate
        for candidate in candidates
        if str(candidate.get("entity_id") or "").strip()
    }
    selected: List[dict] = []
    seen_ids: set[str] = set()
    for pick in parsed.get("picks") or []:
        pick_id = str(pick.get("entity_id") or "").strip().lower()
        if not pick_id or pick_id in seen_ids:
            continue
        candidate = candidates_by_id.get(pick_id)
        if not candidate:
            logger.warning("[REVIEW_POOL] LLM returned unknown entity_id '%s'", pick_id)
            continue
        seen_ids.add(pick_id)
        selected.append(
            _build_selected_row(
                candidate,
                float(pick.get("confidence") or 0.0),
                llm_reason=str(pick.get("reason") or "").strip(),
            )
        )

    selected = selected[:requested]
    selected = _safe_backfill_review_pool(
        selected=selected,
        candidates=candidates,
        min_target=min(requested, 8),
    )
    rejected = _pass_rejected_candidates(
        selected=selected,
        candidates=candidates,
    )
    raw_response = {
        "selected_value_streams": selected,
        "requested_final_output_count": requested,
        "selection_budget": {
            "max_select": max_select,
            "candidate_count": len(candidates),
        },
        "single_review_pool_pass": {
            "selected_value_streams": selected,
            "candidate_count": len(candidates),
            "selection_limits": {"max_select": max_select},
            "candidates_passed": [_to_pass_candidate(row) for row in candidates],
            "rejected_candidates": rejected,
        },
        "missed_strong_candidates": _missed_strong_candidates(candidates, selected),
        "prompt_debug": {
            "prompt_chars": len(prompt),
            "system_prompt_chars": len(system_prompt),
            "candidate_count": len(candidates),
            "idea_card_chars": len(query_for_prompt or ""),
        },
        "timing_ms": {
            "final_llm": llm_elapsed_ms,
            "total": round((perf_counter() - started) * 1000),
        },
    }

    return {
        "selected_value_streams": selected,
        "llm_selected_value_streams": selected,
        "raw_response": raw_response,
        "candidates_used": candidates,
    }


def _empty_pass() -> dict:
    return {
        "selected_value_streams": [],
        "rejected_candidates": [],
        "candidates_passed": [],
        "candidate_count": 0,
        "selection_limits": {"min_select": 0, "max_select": 0},
    }


def _to_pass_candidate(row: dict) -> dict:
    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "description": str(row.get("description") or "").strip(),
        "lane": row.get("lane"),
        "bucket": row.get("bucket"),
        "candidate_lane": row.get("candidate_lane"),
        "candidate_status": row.get("candidate_status"),
        "candidate_status_reason": row.get("candidate_status_reason"),
        "ranking_score": row.get("ranking_score"),
        "semantic_score": row.get("semantic_score"),
        "semantic_rank": row.get("semantic_rank"),
        "historical_strength": row.get("historical_strength"),
        "supporting_ticket_count": row.get("supporting_ticket_count"),
        "support_count": row.get("support_count"),
        "direct_count": row.get("direct_count"),
        "implied_count": row.get("implied_count"),
        "weighted_support": row.get("weighted_support"),
        "weighted_support_count": row.get("weighted_support_count"),
        "best_support_score": row.get("best_support_score"),
        "avg_support_score": row.get("avg_support_score"),
        "from_semantic": row.get("from_semantic"),
        "from_historical": row.get("from_historical"),
        "foundational_signal": bool(row.get("foundational_signal")),
        "foundational_match_text": row.get("foundational_match_text"),
        "foundational_match_type": row.get("foundational_match_type"),
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
        "historical_reasons": list(row.get("historical_reasons") or [])[:3],
    }


def _pass_rejected_candidates(
    *,
    selected: List[dict],
    candidates: List[dict],
) -> List[dict]:
    selected_keys = set()
    for row in selected:
        selected_keys.update(_selection_keys(row))

    rejected: List[dict] = []
    for candidate in candidates:
        if selected_keys.intersection(_selection_keys(candidate)):
            continue
        out = _to_pass_candidate(candidate)
        out["reason"] = "Not selected by the single review-pool LLM pass."
        rejected.append(out)
    return rejected


def _build_selected_row(
    candidate: dict,
    confidence: float,
    llm_reason: str = "",
) -> dict:
    if llm_reason and not _has_score_language(llm_reason):
        reason = llm_reason
    else:
        reason = _business_reason_for_candidate(
            candidate,
            fallback="Selected because the idea card has a defensible business connection to this value stream.",
        )
    return {
        "entity_id": str(candidate.get("entity_id") or "").strip(),
        "entity_name": str(candidate.get("entity_name") or "").strip(),
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": reason,
        "selection_source": "llm_pick",
        "foundational_signal": bool(candidate.get("foundational_signal")),
        "foundational_match_text": candidate.get("foundational_match_text"),
        "foundational_match_type": candidate.get("foundational_match_type"),
        "supporting_ticket_ids": list(candidate.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(candidate.get("supporting_chunk_ids") or [])[:5],
    }


def _safe_backfill_review_pool(
    *,
    selected: List[dict],
    candidates: List[dict],
    min_target: int,
) -> List[dict]:
    """Top up the review pool only when it is too small, and only with safer picks.

    Unlike a full backfill to the requested count, this caps at `min_target` and only
    pulls from `semantic_plus_historical` candidates with either a decent semantic
    score or repeated historical evidence. This avoids padding the pool with weak
    semantic-only candidates that pulled precision down in evaluation.
    """
    if len(selected) >= min_target:
        return selected

    selected_keys: set[str] = set()
    for row in selected:
        selected_keys.update(_selection_keys(row))

    out = list(selected)
    for candidate in _backfill_order(candidates):
        if len(out) >= min_target:
            break
        if selected_keys.intersection(_selection_keys(candidate)):
            continue

        lane = str(candidate.get("lane") or candidate.get("candidate_lane") or "")
        if lane != "semantic_plus_historical":
            continue

        semantic_score = float(candidate.get("semantic_score", 0.0) or 0.0)
        hits = int(candidate.get("supporting_ticket_count", candidate.get("support_count", 0)) or 0)
        if semantic_score < 1.05 and hits < 3:
            continue

        filler = _build_selected_row(
            candidate,
            confidence=0.35,
            llm_reason="Added as a low-confidence review candidate based on combined semantic and historical evidence.",
        )
        filler["selection_source"] = "safe_backfill"
        out.append(filler)
        selected_keys.update(_selection_keys(candidate))

    return out


def _backfill_order(candidates: List[dict]) -> List[dict]:
    lane_priority = {
        "semantic_plus_historical": 0,
        "semantic_only": 1,
        "historical_only": 2,
    }
    return sorted(
        list(candidates),
        key=lambda row: (
            lane_priority.get(
                str(row.get("lane") or row.get("candidate_lane") or "").strip(),
                9,
            ),
            -float(row.get("ranking_score", 0.0) or 0.0),
            -float(row.get("semantic_score", 0.0) or 0.0),
            -float(row.get("best_support_score", 0.0) or 0.0),
            -int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0),
        ),
    )


def _missed_strong_candidates(candidates: List[dict], selected: List[dict]) -> List[dict]:
    selected_keys = set()
    for row in selected:
        selected_keys.update(_selection_keys(row))

    missed: List[dict] = []
    for candidate in candidates:
        if selected_keys.intersection(_selection_keys(candidate)):
            continue

        lane = str(candidate.get("lane") or candidate.get("candidate_lane") or "")
        semantic_score = float(candidate.get("semantic_score", 0.0) or 0.0)
        supporting_count = int(
            candidate.get("supporting_ticket_count", candidate.get("support_count", 0)) or 0
        )
        best_support_score = float(candidate.get("best_support_score", 0.0) or 0.0)

        is_strong = (
            lane == "semantic_plus_historical"
            or bool(candidate.get("foundational_signal"))
            or semantic_score >= 1.20
            or supporting_count >= 5
            or best_support_score >= 0.70
        )
        if not is_strong:
            continue

        missed.append(
            {
                "entity_id": candidate.get("entity_id"),
                "entity_name": candidate.get("entity_name"),
                "lane": lane,
                "semantic_score": semantic_score,
                "supporting_ticket_count": supporting_count,
                "best_support_score": best_support_score,
                "foundational_signal": bool(candidate.get("foundational_signal")),
                "foundational_match_text": candidate.get("foundational_match_text"),
                "foundational_match_type": candidate.get("foundational_match_type"),
                "reason": "Sent to LLM but not selected by single review-pool pass.",
            }
        )

    return missed


def _selection_keys(row: dict) -> set[str]:
    keys: set[str] = set()
    entity_id = _norm_key(str(row.get("entity_id") or ""))
    if entity_id:
        keys.add(f"id:{entity_id}")
    entity_name = _norm_key(
        str(row.get("entity_name") or row.get("value_stream_name") or row.get("name") or "")
    )
    if entity_name:
        keys.add(f"name:{entity_name}")
    return keys


def _business_reason_for_candidate(row: dict, *, fallback: str) -> str:
    name = str(row.get("entity_name") or "").strip()
    description = _sentence_fragment(str(row.get("description") or "").strip())
    analog = _first_analog_summary(row)

    if description and analog:
        return (
            f"Selected because the idea card aligns to {description}. "
            f"Similar prior work shows the same pattern: {analog}"
        )
    if description:
        return f"Selected because the idea card aligns to {description}."
    if analog:
        return f"Selected because similar prior work shows {name or 'this value stream'} recurring in the same business pattern: {analog}"
    return fallback


def _has_score_language(reason: str) -> bool:
    lower = (reason or "").lower()
    return any(
        marker in lower
        for marker in (
            "semantic score",
            "retrieval score",
            "score ",
            "score:",
            "similarity",
            "support count",
            "historical hits",
            "best support",
            "ranking",
            "rank ",
        )
    )


def _first_analog_summary(row: dict) -> str:
    for reason in row.get("historical_reasons") or []:
        clean = _clean_analog_reason(str(reason))
        if clean:
            return clean[:260]
    return ""


def _clean_analog_reason(reason: str) -> str:
    clean = " ".join((reason or "").split())
    clean = re.sub(r"^\[[^\]]+\]\s*", "", clean)
    return clean.strip()


def _sentence_fragment(text: str) -> str:
    fragment = " ".join((text or "").split()).strip()
    if not fragment:
        return ""
    fragment = fragment[:220].rstrip(" ,;:.")
    return fragment[:1].lower() + fragment[1:]


def _norm_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())
