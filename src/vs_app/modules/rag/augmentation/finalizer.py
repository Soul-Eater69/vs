from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import math
import re
from typing import Iterable, List

from .prompt_context import (
    build_direct_candidate_prompt,
    build_historical_gap_prompt,
    build_system_prompt,
)

logger = logging.getLogger(__name__)

_HISTORICAL_GAP_FILL_BUDGET = 4
_CONFIRMED_MERGED_RESCUE_BUDGET = 12
_HISTORICAL_LLM_KEEP_CONFIDENCE = 0.70


def generate_value_streams(
    query_for_prompt: str,
    llm_candidates: List[dict],
    auto_selected: List[dict] | None = None,
    historical_ticket_hits: List[dict] | None = None,
) -> dict:
    from vs_app.integrations.clients.generation_service import GenerationService

    auto_selected = list(auto_selected or [])
    if not llm_candidates:
        return {
            "selected_value_streams": _dedupe_selected(auto_selected),
            "llm_selected_value_streams": [],
            "raw_response": None,
            "candidates_used": [],
        }

    from vs_app.modules.prompts.loader import SelectionResult

    direct_candidates, historical_gap_candidates = _split_llm_candidates(llm_candidates)

    direct_parsed, historical_parsed = _run_selection_passes(
        generation_service_cls=GenerationService,
        output_schema=SelectionResult,
        query_for_prompt=query_for_prompt,
        direct_candidates=direct_candidates,
        historical_gap_candidates=historical_gap_candidates,
        historical_ticket_hits=list(historical_ticket_hits or []),
    )

    llm_selected = _merge_selected(
        list(direct_parsed.get("selected_value_streams") or []),
        list(historical_parsed.get("selected_value_streams") or []),
    )
    (
        final_selected,
        filtered_llm_selected,
        rescued_confirmed_merged,
        rescued_gap_fill,
        dropped_gap_fill,
    ) = _finalize_selected(
        auto_selected=auto_selected,
        llm_selected=llm_selected,
        llm_candidates=llm_candidates,
    )

    parsed = {
        "selected_value_streams": filtered_llm_selected,
        "direct_pass": direct_parsed,
        "historical_gap_pass": historical_parsed,
    }
    parsed["rescued_confirmed_merged"] = rescued_confirmed_merged
    parsed["rescued_historical_gap_fill"] = rescued_gap_fill
    parsed["dropped_historical_gap_fill"] = dropped_gap_fill

    return {
        "selected_value_streams": final_selected,
        "llm_selected_value_streams": llm_selected,
        "rescued_confirmed_merged_value_streams": rescued_confirmed_merged,
        "rescued_historical_gap_fill_value_streams": rescued_gap_fill,
        "dropped_historical_gap_fill_value_streams": dropped_gap_fill,
        "raw_response": parsed,
        "candidates_used": llm_candidates,
    }


def _run_selection_passes(
    *,
    generation_service_cls,
    output_schema,
    query_for_prompt: str,
    direct_candidates: List[dict],
    historical_gap_candidates: List[dict],
    historical_ticket_hits: List[dict],
) -> tuple[dict, dict]:
    def run_direct() -> dict:
        if not direct_candidates:
            return {"selected_value_streams": [], "rejected_candidates": []}
        return _run_selection_pass(
            gen_svc=generation_service_cls(),
            output_schema=output_schema,
            query_for_prompt=query_for_prompt,
            candidates=direct_candidates,
            prompt_kind="direct",
        )

    def run_historical_gap() -> dict:
        if not historical_gap_candidates:
            return {"selected_value_streams": [], "rejected_candidates": []}
        return _run_selection_pass(
            gen_svc=generation_service_cls(),
            output_schema=output_schema,
            query_for_prompt=query_for_prompt,
            candidates=historical_gap_candidates,
            precedent_candidates=historical_gap_candidates,
            historical_ticket_hits=historical_ticket_hits,
            prompt_kind="historical_gap",
        )

    if direct_candidates and historical_gap_candidates:
        with ThreadPoolExecutor(max_workers=2) as executor:
            direct_future = executor.submit(run_direct)
            historical_future = executor.submit(run_historical_gap)
            return direct_future.result(), historical_future.result()

    return run_direct(), run_historical_gap()


def _run_selection_pass(
    *,
    gen_svc,
    output_schema,
    query_for_prompt: str,
    candidates: List[dict],
    prompt_kind: str,
    precedent_candidates: List[dict] | None = None,
    historical_ticket_hits: List[dict] | None = None,
) -> dict:
    from ..ranking.reranker import sanitize_selected

    if not candidates:
        return {"selected_value_streams": [], "rejected_candidates": []}

    if prompt_kind == "historical_gap":
        prompt = build_historical_gap_prompt(
            query_for_prompt=query_for_prompt,
            candidates=candidates,
            precedent_candidates=precedent_candidates,
            historical_ticket_hits=historical_ticket_hits,
        )
        system_prompt = build_system_prompt(min_select=0, max_select=12)
    else:
        prompt = build_direct_candidate_prompt(
            query_for_prompt=query_for_prompt,
            candidates=candidates,
        )
        system_prompt = build_system_prompt(
            min_select=4,
            max_select=_direct_selection_max(candidates),
        )

    result = gen_svc.generate_structured(
        query=prompt,
        output_schema=output_schema,
        system_prompt=system_prompt,
    )
    parsed = result.model_dump() if hasattr(result, "model_dump") else {"selected_value_streams": []}
    return sanitize_selected(parsed, candidates)


def _direct_selection_max(candidates: int | List[dict]) -> int:
    if isinstance(candidates, int):
        return min(22, max(12, math.ceil(max(0, candidates) * 0.65)))

    candidate_count = len(candidates)
    strong_count = sum(1 for row in candidates if _is_strong_direct_prompt_candidate(row))
    scaled_cap = math.ceil(candidate_count * 0.65)
    evidence_cap = strong_count + 4
    return min(22, max(12, min(scaled_cap, evidence_cap)))


def _split_llm_candidates(llm_candidates: List[dict]) -> tuple[List[dict], List[dict]]:
    direct_candidates: List[dict] = []
    historical_gap_candidates: List[dict] = []
    for row in llm_candidates:
        lane = str(row.get("candidate_lane") or "")
        is_historical_only = bool(row.get("from_historical")) and not bool(row.get("from_semantic"))
        if lane == "historical_recall" or is_historical_only:
            historical_gap_candidates.append(row)
        else:
            direct_candidates.append(row)
    return direct_candidates, historical_gap_candidates


def _finalize_selected(
    *,
    auto_selected: List[dict],
    llm_selected: List[dict],
    llm_candidates: List[dict],
    confirmed_merged_rescue_budget: int = _CONFIRMED_MERGED_RESCUE_BUDGET,
    historical_gap_fill_budget: int = _HISTORICAL_GAP_FILL_BUDGET,
) -> tuple[List[dict], List[dict], List[dict], List[dict], List[dict]]:
    candidates_by_key = _candidate_lookup(llm_candidates)
    filtered_llm_selected: List[dict] = []
    dropped_gap_fill: List[dict] = []

    for row in llm_selected:
        candidate = _candidate_for_selection(row, candidates_by_key)
        if (
            _is_historical_gap_fill_candidate(candidate)
            and not _passes_gap_fill_evidence(candidate)
            and not _passes_high_confidence_historical_selection(row, candidate)
        ):
            dropped_gap_fill.append(_with_gap_fill_reason(row, candidate, "weak_historical_gap_fill_evidence"))
            continue
        if _is_confirmed_merged_candidate(candidate) and not _passes_confirmed_llm_evidence(candidate):
            continue
        filtered_llm_selected.append(_rewrite_score_reason(row, candidate))

    selected_so_far = _merge_selected(auto_selected, filtered_llm_selected)
    rescued_confirmed_merged = _rescue_confirmed_merged(
        llm_candidates=llm_candidates,
        already_selected=selected_so_far,
        limit=confirmed_merged_rescue_budget,
    )
    selected_so_far = _merge_selected(selected_so_far, rescued_confirmed_merged)
    existing_gap_fill_count = sum(
        1
        for row in selected_so_far
        if _is_historical_gap_fill_candidate(_candidate_for_selection(row, candidates_by_key))
    )
    remaining_budget = max(0, historical_gap_fill_budget - existing_gap_fill_count)
    rescued_gap_fill = _rescue_historical_gap_fill(
        llm_candidates=llm_candidates,
        already_selected=selected_so_far,
        limit=remaining_budget,
    )
    final_selected = _merge_selected(selected_so_far, rescued_gap_fill)
    return final_selected, filtered_llm_selected, rescued_confirmed_merged, rescued_gap_fill, dropped_gap_fill


def _merge_selected(auto_selected: List[dict], llm_selected: List[dict]) -> List[dict]:
    merged = {}
    for row in list(auto_selected) + list(llm_selected):
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key not in merged:
            merged[key] = dict(row)
            continue

        current = merged[key]
        current["confidence"] = max(
            float(current.get("confidence", 0.0) or 0.0),
            float(row.get("confidence", 0.0) or 0.0),
        )
        reasons = [str(current.get("reason") or "").strip(), str(row.get("reason") or "").strip()]
        reasons = [text for text in reasons if text]
        current["reason"] = " ".join(dict.fromkeys(reasons))

        for field in ("supporting_ticket_ids", "supporting_chunk_ids"):
            merged_values = list(current.get(field) or [])
            for value in row.get(field) or []:
                if value not in merged_values:
                    merged_values.append(value)
            current[field] = merged_values[:5]

    return _dedupe_selected(merged.values())


def _rescue_historical_gap_fill(
    *,
    llm_candidates: List[dict],
    already_selected: List[dict],
    limit: int,
) -> List[dict]:
    if limit <= 0:
        return []

    selected_keys = {
        _norm_key(str(row.get("entity_name") or ""))
        for row in already_selected
        if str(row.get("entity_name") or "").strip()
    }
    rescue_pool = [
        row
        for row in llm_candidates
        if _is_historical_gap_fill_candidate(row)
        and _passes_gap_fill_evidence(row)
        and _norm_key(str(row.get("entity_name") or "")) not in selected_keys
    ]
    rescue_pool.sort(key=_gap_fill_priority, reverse=True)
    return [_to_gap_fill_selected(row) for row in rescue_pool[:limit]]


def _rescue_confirmed_merged(
    *,
    llm_candidates: List[dict],
    already_selected: List[dict],
    limit: int,
) -> List[dict]:
    if limit <= 0:
        return []

    selected_keys = {
        _norm_key(str(row.get("entity_name") or ""))
        for row in already_selected
        if str(row.get("entity_name") or "").strip()
    }
    rescue_pool = [
        row
        for row in llm_candidates
        if _is_confirmed_merged_candidate(row)
        and _passes_confirmed_merged_evidence(row)
        and _norm_key(str(row.get("entity_name") or "")) not in selected_keys
    ]
    rescue_pool.sort(key=_confirmed_merged_priority, reverse=True)
    return [_to_confirmed_merged_selected(row) for row in rescue_pool[:limit]]


def _candidate_lookup(candidates: List[dict]) -> dict[str, dict]:
    lookup: dict[str, dict] = {}
    for row in candidates:
        name_key = _norm_key(str(row.get("entity_name") or ""))
        id_key = _norm_key(str(row.get("entity_id") or ""))
        if name_key:
            lookup[f"name:{name_key}"] = row
        if id_key:
            lookup[f"id:{id_key}"] = row
    return lookup


def _candidate_for_selection(row: dict | None, lookup: dict[str, dict]) -> dict:
    if not row:
        return {}
    entity_id = _norm_key(str(row.get("entity_id") or ""))
    entity_name = _norm_key(str(row.get("entity_name") or ""))
    return lookup.get(f"id:{entity_id}") or lookup.get(f"name:{entity_name}") or {}


def _is_historical_gap_fill_candidate(row: dict | None) -> bool:
    if not row:
        return False
    return (
        str(row.get("candidate_lane") or "") == "historical_recall"
        and bool(row.get("from_historical"))
        and not bool(row.get("from_semantic"))
    )


def _is_confirmed_merged_candidate(row: dict | None) -> bool:
    if not row:
        return False
    return (
        str(row.get("candidate_lane") or "") == "confirmed_direct"
        and bool(row.get("from_historical"))
        and bool(row.get("from_semantic"))
    )


def _passes_confirmed_merged_evidence(row: dict | None) -> bool:
    if not row:
        return False
    support_count = int(row.get("support_count", 0) or 0)
    semantic_score = float(row.get("semantic_score", 0.0) or 0.0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    weighted_support = float(row.get("weighted_support_count", support_count) or 0.0)

    if weighted_support < 0.75:
        return False
    if support_count >= 5 and semantic_score >= 1.20 and best_score >= 0.60:
        return True
    if support_count >= 5 and semantic_score >= 1.00:
        return True
    return support_count >= 3 and semantic_score >= 1.35 and best_score >= 0.65


def _passes_confirmed_llm_evidence(row: dict | None) -> bool:
    if not row:
        return False
    support_count = int(row.get("support_count", 0) or 0)
    semantic_score = float(row.get("semantic_score", 0.0) or 0.0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    weighted_support = float(row.get("weighted_support_count", support_count) or 0.0)

    if semantic_score >= 1.35:
        return True
    if semantic_score >= 1.25 and best_score >= 0.58:
        return True
    if support_count >= 5 and semantic_score >= 1.20 and best_score >= 0.60:
        return True
    if support_count >= 8 and semantic_score >= 1.10 and best_score >= 0.60 and weighted_support >= 0.75:
        return True
    return False


def _is_strong_direct_prompt_candidate(row: dict) -> bool:
    lane = str(row.get("candidate_lane") or "")
    if lane == "confirmed_direct":
        return _passes_confirmed_llm_evidence(row)
    if lane == "semantic_direct":
        return float(row.get("semantic_score", 0.0) or 0.0) >= 1.35
    return False


def _passes_gap_fill_evidence(row: dict | None) -> bool:
    if not row:
        return False
    support_count = int(row.get("support_count", 0) or 0)
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    avg_score = float(row.get("avg_support_score", 0.0) or 0.0)
    weighted_support = float(row.get("weighted_support_count", 0.0) or 0.0)
    ticket_count = len({str(tid).strip() for tid in (row.get("supporting_ticket_ids") or []) if str(tid).strip()})

    if (
        ticket_count == 1
        and direct_count >= 4
        and support_count >= 4
        and best_score >= 0.62
        and avg_score >= 0.56
        and weighted_support >= 0.75
    ):
        return True

    if (
        support_count >= 5
        and direct_count >= 3
        and best_score >= 0.62
        and avg_score >= 0.56
        and weighted_support >= 0.60
    ):
        return True

    if (
        support_count >= 8
        and implied_count >= 5
        and best_score >= 0.60
        and avg_score >= 0.55
        and weighted_support >= 0.60
    ):
        return True

    if (
        support_count >= 3
        and ticket_count >= 2
        and best_score >= 0.70
        and avg_score >= 0.58
        and weighted_support >= 0.75
        and (direct_count >= 1 or implied_count >= 3)
    ):
        return True

    return (
        support_count >= 5
        and ticket_count >= 2
        and best_score >= 0.64
        and avg_score >= 0.56
        and weighted_support >= 0.75
        and (direct_count >= 1 or implied_count >= 3)
    )


def _passes_high_confidence_historical_selection(selection: dict, candidate: dict | None) -> bool:
    if not candidate:
        return False
    confidence = float(selection.get("confidence", 0.0) or 0.0)
    return confidence >= _HISTORICAL_LLM_KEEP_CONFIDENCE


def _gap_fill_priority(row: dict) -> tuple[float, float, float, int, int]:
    return (
        float(row.get("best_support_score", 0.0) or 0.0),
        float(row.get("avg_support_score", 0.0) or 0.0),
        float(row.get("weighted_support_count", 0.0) or 0.0),
        int(row.get("direct_count", 0) or 0),
        int(row.get("support_count", 0) or 0),
    )


def _confirmed_merged_priority(row: dict) -> tuple[int, float, float, float]:
    return (
        int(row.get("support_count", 0) or 0),
        float(row.get("best_support_score", 0.0) or 0.0),
        float(row.get("semantic_score", 0.0) or 0.0),
        float(row.get("ranking_score", 0.0) or 0.0),
    )


def _to_confirmed_merged_selected(row: dict) -> dict:
    support_count = int(row.get("support_count", 0) or 0)
    semantic_score = float(row.get("semantic_score", 0.0) or 0.0)
    confidence = min(0.90, 0.52 + 0.08 * min(support_count, 4) + 0.06 * min(semantic_score, 2.0))
    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "confidence": round(confidence, 4),
        "reason": _business_reason_for_candidate(
            row,
            fallback=(
                "Selected because the idea card directly aligns to this value stream and similar "
                "prior work shows the same business pattern."
            ),
        ),
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
        "selection_source": "confirmed_merged_rescue",
    }


def _to_gap_fill_selected(row: dict) -> dict:
    support_count = int(row.get("support_count", 0) or 0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    confidence = min(0.84, 0.44 + 0.08 * min(support_count, 4) + 0.08 * min(best_score, 1.0))
    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "confidence": round(confidence, 4),
        "reason": _business_reason_for_candidate(
            row,
            fallback=(
                "Selected as an implied value stream because similar prior tickets show this "
                "downstream business workflow recurring with the same initiative pattern."
            ),
        ),
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
        "selection_source": "historical_gap_fill_rescue",
    }


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


def _rewrite_score_reason(row: dict, candidate: dict) -> dict:
    reason = str(row.get("reason") or "")
    if not _has_score_language(reason):
        return row

    out = dict(row)
    out["reason"] = _business_reason_for_candidate(
        candidate,
        fallback="Selected because the idea card has a defensible business connection to this value stream.",
    )
    return out


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


def _with_gap_fill_reason(row: dict, candidate: dict, reason: str) -> dict:
    out = dict(row)
    out["drop_reason"] = reason
    if candidate:
        out["candidate_evidence"] = {
            "support_count": candidate.get("support_count", 0),
            "direct_count": candidate.get("direct_count", 0),
            "implied_count": candidate.get("implied_count", 0),
            "weighted_support_count": candidate.get("weighted_support_count", 0.0),
            "best_support_score": candidate.get("best_support_score", 0.0),
            "avg_support_score": candidate.get("avg_support_score", 0.0),
        }
    return out


def _norm_key(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _dedupe_selected(rows: Iterable[dict]) -> List[dict]:
    out: List[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out
