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


def generate_value_streams(
    query_for_prompt: str,
    llm_candidates: List[dict],
    auto_selected: List[dict] | None = None,
    historical_ticket_hits: List[dict] | None = None,
    final_output_count: int | None = None,
) -> dict:
    auto_selected_ignored = list(auto_selected or [])
    if not llm_candidates:
        budget = _selection_budget(
            final_output_count=final_output_count,
            direct_candidate_count=0,
            historical_candidate_count=0,
        )
        return {
            "selected_value_streams": [],
            "llm_selected_value_streams": [],
            "rescued_confirmed_merged_value_streams": [],
            "rescued_historical_gap_fill_value_streams": [],
            "dropped_historical_gap_fill_value_streams": [],
            "raw_response": {
                "selected_value_streams": [],
                "direct_pass": _empty_pass(),
                "historical_gap_pass": _empty_pass(),
                "auto_selected_ignored": auto_selected_ignored,
                "requested_final_output_count": final_output_count,
                "selection_budget": budget,
            },
            "candidates_used": [],
        }

    from vs_app.integrations.clients.generation_service import GenerationService
    from vs_app.modules.prompts.loader import SelectionResult

    direct_candidates, historical_gap_candidates = _split_llm_candidates(llm_candidates)
    budget = _selection_budget(
        final_output_count=final_output_count,
        direct_candidate_count=len(direct_candidates),
        historical_candidate_count=len(historical_gap_candidates),
    )

    direct_parsed, historical_parsed = _run_selection_passes(
        generation_service_cls=GenerationService,
        output_schema=SelectionResult,
        query_for_prompt=query_for_prompt,
        direct_candidates=direct_candidates,
        historical_gap_candidates=historical_gap_candidates,
        historical_ticket_hits=list(historical_ticket_hits or []),
        selection_budget=budget,
    )

    llm_selected = _merge_selected(
        list(direct_parsed.get("selected_value_streams") or []),
        list(historical_parsed.get("selected_value_streams") or []),
    )
    final_selected = _merge_selected([], llm_selected)

    raw_response = {
        "selected_value_streams": final_selected,
        "direct_pass": direct_parsed,
        "historical_gap_pass": historical_parsed,
        "auto_selected_ignored": auto_selected_ignored,
        "requested_final_output_count": final_output_count,
        "selection_budget": budget,
    }

    return {
        "selected_value_streams": final_selected,
        "llm_selected_value_streams": llm_selected,
        "rescued_confirmed_merged_value_streams": [],
        "rescued_historical_gap_fill_value_streams": [],
        "dropped_historical_gap_fill_value_streams": [],
        "raw_response": raw_response,
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
    selection_budget: dict,
) -> tuple[dict, dict]:
    def run_direct() -> dict:
        if not direct_candidates:
            return _empty_pass()
        return _run_selection_pass(
            gen_svc=generation_service_cls(),
            output_schema=output_schema,
            query_for_prompt=query_for_prompt,
            candidates=direct_candidates,
            prompt_kind="direct",
            min_select=selection_budget["direct_min_select"],
            max_select=selection_budget["direct_max_select"],
        )

    def run_historical_gap() -> dict:
        if not historical_gap_candidates:
            return _empty_pass()
        return _run_selection_pass(
            gen_svc=generation_service_cls(),
            output_schema=output_schema,
            query_for_prompt=query_for_prompt,
            candidates=historical_gap_candidates,
            precedent_candidates=historical_gap_candidates,
            historical_ticket_hits=historical_ticket_hits,
            prompt_kind="historical_gap",
            min_select=selection_budget["historical_min_select"],
            max_select=selection_budget["historical_max_select"],
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
    min_select: int | None = None,
    max_select: int | None = None,
) -> dict:
    from ..ranking.reranker import sanitize_selected

    if not candidates:
        return _empty_pass()

    if prompt_kind == "historical_gap":
        min_select = 0 if min_select is None else min_select
        max_select = _historical_gap_selection_max(candidates) if max_select is None else max_select
        prompt = build_historical_gap_prompt(
            query_for_prompt=query_for_prompt,
            candidates=candidates,
            precedent_candidates=precedent_candidates,
            historical_ticket_hits=historical_ticket_hits,
        )
    else:
        if min_select is None or max_select is None:
            min_select, max_select = _direct_selection_bounds(candidates)
        prompt = build_direct_candidate_prompt(
            query_for_prompt=query_for_prompt,
            candidates=candidates,
        )

    result = gen_svc.generate_structured(
        query=prompt,
        output_schema=output_schema,
        system_prompt=build_system_prompt(min_select=min_select, max_select=max_select),
    )
    parsed = result.model_dump() if hasattr(result, "model_dump") else {"selected_value_streams": []}
    parsed = sanitize_selected(parsed, candidates)
    candidates_by_key = _candidate_lookup(candidates)
    parsed["selected_value_streams"] = [
        _rewrite_score_reason(row, _candidate_for_selection(row, candidates_by_key))
        for row in parsed.get("selected_value_streams", [])
    ]
    parsed["candidates_passed"] = [_to_pass_candidate(row) for row in candidates]
    parsed["candidate_count"] = len(candidates)
    parsed["selection_limits"] = {
        "min_select": min_select,
        "max_select": max_select,
    }
    parsed["rejected_candidates"] = _pass_rejected_candidates(
        selected=parsed.get("selected_value_streams") or [],
        candidates=candidates,
        prompt_kind=prompt_kind,
    )
    return parsed


def _direct_selection_max(candidates: int | List[dict]) -> int:
    candidate_count = candidates if isinstance(candidates, int) else len(candidates)
    if candidate_count <= 0:
        return 0
    return min(22, max(6, math.ceil(candidate_count * 0.50)))


def _direct_selection_bounds(candidates: int | List[dict]) -> tuple[int, int]:
    max_select = _direct_selection_max(candidates)
    if max_select <= 0:
        return 0, 0
    min_select = min(3, max(1, math.ceil(max_select * 0.20)))
    return min_select, max_select


def _historical_gap_selection_max(candidates: int | List[dict]) -> int:
    candidate_count = candidates if isinstance(candidates, int) else len(candidates)
    if candidate_count <= 0:
        return 0
    return min(8, max(3, math.ceil(candidate_count * 0.35)))


def _selection_budget(
    *,
    final_output_count: int | None,
    direct_candidate_count: int,
    historical_candidate_count: int,
) -> dict:
    if final_output_count is None:
        direct_min, direct_max = _direct_selection_bounds(direct_candidate_count)
        historical_min = 0
        historical_max = _historical_gap_selection_max(historical_candidate_count)
        return {
            "direct_min_select": min(direct_min, direct_candidate_count),
            "direct_max_select": min(direct_max, direct_candidate_count),
            "historical_min_select": historical_min,
            "historical_max_select": min(historical_max, historical_candidate_count),
            "total_max_select": min(direct_max, direct_candidate_count)
            + min(historical_max, historical_candidate_count),
        }

    requested = max(1, int(final_output_count))

    if direct_candidate_count <= 0:
        historical_max = min(requested, historical_candidate_count)
        return {
            "direct_min_select": 0,
            "direct_max_select": 0,
            "historical_min_select": historical_max,
            "historical_max_select": historical_max,
            "total_max_select": historical_max,
        }

    if historical_candidate_count <= 0:
        direct_max = min(requested, direct_candidate_count)
        return {
            "direct_min_select": direct_max,
            "direct_max_select": direct_max,
            "historical_min_select": 0,
            "historical_max_select": 0,
            "total_max_select": direct_max,
        }

    direct_max = min(direct_candidate_count, max(1, math.ceil(requested * 0.70)))
    historical_max = min(historical_candidate_count, max(0, requested - direct_max))

    remaining = requested - (direct_max + historical_max)
    if remaining > 0:
        direct_room = max(0, direct_candidate_count - direct_max)
        direct_add = min(direct_room, remaining)
        direct_max += direct_add
        remaining -= direct_add
    if remaining > 0:
        historical_max = min(historical_candidate_count, historical_max + remaining)

    direct_min = min(direct_max, max(1, math.floor(direct_max * 0.60)))
    historical_min = min(historical_max, max(0, math.floor(historical_max * 0.50)))

    return {
        "direct_min_select": direct_min,
        "direct_max_select": direct_max,
        "historical_min_select": historical_min,
        "historical_max_select": historical_max,
        "total_max_select": direct_max + historical_max,
    }


def _split_llm_candidates(llm_candidates: List[dict]) -> tuple[List[dict], List[dict]]:
    direct_candidates: List[dict] = []
    historical_candidates: List[dict] = []

    for row in llm_candidates:
        lane = str(row.get("lane") or row.get("candidate_lane") or row.get("bucket") or "")
        if lane == "historical_only":
            historical_candidates.append(row)
        else:
            direct_candidates.append(row)

    return direct_candidates, historical_candidates


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
        current["reason"] = " ".join(dict.fromkeys(text for text in reasons if text))

        for field in ("supporting_ticket_ids", "supporting_chunk_ids"):
            merged_values = list(current.get(field) or [])
            for value in row.get(field) or []:
                if value not in merged_values:
                    merged_values.append(value)
            current[field] = merged_values[:5]

    return _dedupe_selected(merged.values())


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
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
        "historical_reasons": list(row.get("historical_reasons") or [])[:3],
    }


def _pass_rejected_candidates(
    *,
    selected: List[dict],
    candidates: List[dict],
    prompt_kind: str,
) -> List[dict]:
    selected_keys = set()
    for row in selected:
        selected_keys.update(_selection_keys(row))

    rejected: List[dict] = []
    for candidate in candidates:
        if selected_keys.intersection(_selection_keys(candidate)):
            continue
        out = _to_pass_candidate(candidate)
        out["reason"] = (
            "Not selected by the historical LLM pass."
            if prompt_kind == "historical_gap"
            else "Not selected by the direct LLM pass."
        )
        rejected.append(out)
    return rejected


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
