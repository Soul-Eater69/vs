from __future__ import annotations

import logging
import math
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


def merge_candidate_sources(
    semantic_candidates: List[dict],
    historical_support: List[dict],
    *,
    max_llm_candidates: int = 24,
    strong_support_count: int = 6,
    strong_support_score: float = 0.78,
    strong_confirmed_support_count: int = 4,
    strong_confirmed_support_score: float = 0.70,
    moderate_support_count: int = 2,
    moderate_support_score: float = 0.45,
    strong_implied_support_count: int = 8,
    strong_implied_support_score: float = 0.75,
) -> dict:
    by_name: Dict[str, dict] = {}

    for row in semantic_candidates:
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        by_name[_norm_name(name)] = {
            "entity_id": str(row.get("entity_id") or "").strip(),
            "entity_name": name,
            "description": str(row.get("description") or "").strip(),
            "semantic_score": float(row.get("semantic_score", 0.0) or 0.0),
            "from_semantic": True,
            "from_historical": False,
            "support_count": 0,
            "direct_count": 0,
            "implied_count": 0,
            "weighted_support_count": 0.0,
            "weighted_direct_count": 0.0,
            "weighted_implied_count": 0.0,
            "best_support_score": 0.0,
            "avg_support_score": 0.0,
            "supporting_ticket_ids": [],
            "supporting_chunk_ids": [],
            "historical_reasons": [],
            "label_sources": [],
            "bucket": "semantic_only",
            "ranking_score": 0.0,
            "candidate_lane": "unassigned",
            "candidate_status": "unclassified",
            "candidate_status_reason": "",
        }

    for row in historical_support:
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        key = _norm_name(name)
        existing = by_name.get(key)
        if existing is None:
            existing = {
                "entity_id": str(row.get("entity_id") or "").strip(),
                "entity_name": name,
                "description": "",
                "semantic_score": 0.0,
                "from_semantic": False,
                "from_historical": True,
                "support_count": 0,
                "direct_count": 0,
                "implied_count": 0,
                "weighted_support_count": 0.0,
                "weighted_direct_count": 0.0,
                "weighted_implied_count": 0.0,
                "best_support_score": 0.0,
                "avg_support_score": 0.0,
                "supporting_ticket_ids": [],
                "supporting_chunk_ids": [],
                "historical_reasons": [],
                "label_sources": [],
                "bucket": "historical_only",
                "ranking_score": 0.0,
                "candidate_lane": "unassigned",
                "candidate_status": "unclassified",
                "candidate_status_reason": "",
            }
            by_name[key] = existing

        existing["from_historical"] = True
        existing["support_count"] = int(row.get("support_count", 0) or 0)
        existing["direct_count"] = int(row.get("direct_count", 0) or 0)
        existing["implied_count"] = int(row.get("implied_count", 0) or 0)
        existing["weighted_support_count"] = float(row.get("weighted_support_count", 0.0) or 0.0)
        existing["weighted_direct_count"] = float(row.get("weighted_direct_count", 0.0) or 0.0)
        existing["weighted_implied_count"] = float(row.get("weighted_implied_count", 0.0) or 0.0)
        existing["best_support_score"] = float(row.get("best_support_score", 0.0) or 0.0)
        existing["avg_support_score"] = float(row.get("avg_support_score", 0.0) or 0.0)
        existing["supporting_ticket_ids"] = list(row.get("supporting_ticket_ids") or [])
        existing["supporting_chunk_ids"] = list(row.get("supporting_chunk_ids") or [])
        existing["historical_reasons"] = list(row.get("historical_reasons") or [])[:3]
        existing["label_sources"] = list(row.get("label_sources") or [])
        if not existing.get("entity_id"):
            existing["entity_id"] = str(row.get("entity_id") or "").strip()

    merged = list(by_name.values())
    for row in merged:
        if row["from_semantic"] and row["from_historical"]:
            row["bucket"] = "semantic_plus_historical"
        elif row["from_semantic"]:
            row["bucket"] = "semantic_only"
        else:
            row["bucket"] = "historical_only"
        row["historical_strength"] = _historical_strength(row)
        row["ranking_score"] = _ranking_score(row)
        row["candidate_lane"] = _candidate_lane(row)

    merged.sort(
        key=lambda row: float(row.get("ranking_score", 0.0) or 0.0),
        reverse=True,
    )

    auto_selected: List[dict] = []
    llm_candidate_pool: Dict[str, List[dict]] = {
        "confirmed_direct": [],
        "historical_recall": [],
        "semantic_direct": [],
    }

    for row in merged:
        if _should_auto_include_merged(
            row,
            support_count=strong_confirmed_support_count,
            min_score=strong_confirmed_support_score,
        ):
            row["candidate_status"] = "auto_selected"
            row["candidate_status_reason"] = "cross_confirmed_semantic_and_historical"
            auto_selected.append(_to_selected_merged(row))
            continue

        if _should_auto_include(
            row,
            support_count=strong_support_count,
            min_score=strong_support_score,
            strong_implied_count=strong_implied_support_count,
            strong_implied_score=strong_implied_support_score,
        ):
            row["candidate_status"] = "auto_selected"
            row["candidate_status_reason"] = "strong_historical_support"
            auto_selected.append(_to_selected_value_stream(row))
            continue

        lane = str(row.get("candidate_lane") or "")
        if lane == "confirmed_direct":
            llm_candidate_pool[lane].append(row)
            continue

        if lane == "historical_recall" and _should_send_historical_recall_to_llm(
            row,
            support_count=moderate_support_count,
            min_score=moderate_support_score,
        ):
            llm_candidate_pool[lane].append(row)
            continue

        if lane == "semantic_direct" and _should_send_semantic_to_llm(row):
            llm_candidate_pool[lane].append(row)
            continue

        row["candidate_status"] = "dropped_before_llm"
        row["candidate_status_reason"] = "insufficient_support"

    llm_candidates = _select_llm_candidates(
        merged,
        llm_candidate_pool,
        max_llm_candidates=max_llm_candidates,
    )

    logger.info(
        "[HIST-RAG] %d merged candidates -> %d auto-selected, %d for LLM (cap=%d)",
        len(merged),
        len(auto_selected),
        len(llm_candidates),
        max_llm_candidates,
    )

    return {
        "merged_candidates": merged,
        "auto_selected_value_streams": auto_selected,
        "llm_candidates": llm_candidates,
    }


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _historical_strength(row: dict) -> float:
    return (
        float(row.get("best_support_score", 0.0) or 0.0)
        + 0.18 * _weighted_support_value(row, "weighted_direct_count", "direct_count")
        + 0.06 * _weighted_support_value(row, "weighted_implied_count", "implied_count")
        + _label_source_adjustment(row)
    )


def _candidate_lane(row: dict) -> str:
    if row.get("from_semantic") and row.get("from_historical"):
        return "confirmed_direct"
    if row.get("from_historical"):
        return "historical_recall"
    if row.get("from_semantic"):
        return "semantic_direct"
    return "weak_noise"


def _ranking_score(row: dict) -> float:
    semantic = float(row.get("semantic_score", 0.0) or 0.0)
    hist = float(row.get("historical_strength", 0.0) or 0.0)
    if row.get("from_semantic") and row.get("from_historical"):
        return semantic + 0.25 * hist
    if row.get("from_semantic"):
        return semantic
    return 0.70 * hist


def _label_source_adjustment(row: dict) -> float:
    sources = {str(value).strip() for value in (row.get("label_sources") or []) if str(value).strip()}
    if not sources:
        return 0.0
    if "jira_issuelinks" in sources:
        return 0.06
    if sources == {"jira_themes_fallback"}:
        return -0.04
    return 0.0


def _should_auto_include(
    row: dict,
    *,
    support_count: int,
    min_score: float,
    strong_implied_count: int,
    strong_implied_score: float,
) -> bool:
    """Tier 2 (direct-tagged) and Tier 3 (heavy-implied) for historical-only rows.

    No moderate fallback — anything that doesn't pass one of the named tiers
    is sent to the LLM for judgment.
    """
    if row.get("from_semantic"):
        return False

    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    avg_score = float(row.get("avg_support_score", 0.0) or 0.0)
    direct_count = int(row.get("direct_count", 0) or 0)
    total_count = int(row.get("support_count", 0) or 0)
    weighted_total = _weighted_support_value(row, "weighted_support_count", "support_count")

    # Tier 2: direct jira-tagged consensus. 3+ analog tickets explicitly
    # tagged this VS in jira AND total support is meaningful AND peak score
    # is reasonable.
    if (
        direct_count >= 4
        and total_count >= support_count
        and best_score >= min_score
        and avg_score >= 0.65
        and weighted_total >= 2.0
    ):
        return True

    # Tier 3: heavy implied consensus. Many analogs, high peak, high
    # average (this avg gate is the key discriminator — TPs have
    # consistently-similar analogs, FPs have one peak hit and a noisy
    # tail), and concentrated weighting (filters out broadly-tagged
    # tickets).
    if (
        total_count >= strong_implied_count
        and best_score >= strong_implied_score
        and avg_score >= 0.65
        and weighted_total >= 2.5
    ):
        return True

    return False


def _should_auto_include_merged(row: dict, *, support_count: int, min_score: float) -> bool:
    """Tier 1: cross-confirmed (semantic + historical agree, both strong)."""
    if not (row.get("from_semantic") and row.get("from_historical")):
        return False
    if float(row.get("semantic_score", 0.0) or 0.0) < 1.5:
        return False
    if float(row.get("best_support_score", 0.0) or 0.0) < min_score:
        return False
    if int(row.get("support_count", 0) or 0) < support_count:
        return False
    return True


def _to_selected_merged(row: dict) -> dict:
    semantic_score = float(row.get("semantic_score", 0.0) or 0.0)
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")
    confidence = min(0.95, 0.55 + 0.10 * min(semantic_score, 2.0) + 0.08 * min(weighted_support, 3.0))

    reason = _business_reason_for_candidate(
        row,
        fallback=(
            "Selected because the idea card directly aligns to this value stream and similar "
            "prior tickets show the same business pattern."
        ),
    )

    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "confidence": round(confidence, 4),
        "reason": reason,
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
    }


def _should_send_historical_recall_to_llm(row: dict, *, support_count: int, min_score: float) -> bool:
    """Admit historical-recall candidates based on repeated coherent analog patterns."""
    total_count = int(row.get("support_count", 0) or 0)
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    avg_score = float(row.get("avg_support_score", 0.0) or 0.0)
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")

    if direct_count >= 2 and best_score >= max(min_score, 0.55):
        return True

    if total_count >= 3 and best_score >= 0.70 and avg_score >= 0.58:
        return True

    if implied_count >= 5 and best_score >= 0.72 and avg_score >= 0.60:
        return True

    # Source-ticket exclusion removes the strongest neighbor during leave-one-out
    # testing. Keep repeated, coherent moderate evidence in the LLM lane so the
    # historical pass can still adjudicate it instead of silently dropping it.
    if (
        total_count >= 5
        and best_score >= 0.60
        and avg_score >= 0.55
        and (direct_count >= 1 or implied_count >= 5)
    ):
        return True

    if total_count >= 8 and best_score >= 0.60 and avg_score >= 0.52:
        return True

    if total_count >= support_count:
        return weighted_support >= max(1.0, support_count * 0.4)
    return best_score >= min_score and weighted_support >= 0.5


def _should_send_semantic_to_llm(row: dict) -> bool:
    """Keep the direct-semantic lane focused on candidates with meaningful relevance."""
    if not row.get("from_semantic"):
        return False
    return float(row.get("semantic_score", 0.0) or 0.0) >= 0.95


def _to_selected_value_stream(row: dict) -> dict:
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")
    confidence = min(0.92, 0.38 + 0.12 * min(weighted_support, 4.0) + 0.10 * min(best_score, 1.0))

    reason = _business_reason_for_candidate(
        row,
        fallback=(
            "Selected as a historical recovery because similar prior tickets show this "
            "business workflow recurring with the same initiative pattern."
        ),
    )

    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "confidence": round(confidence, 4),
        "reason": reason,
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
    }


def _weighted_support_value(row: dict, weighted_key: str, fallback_key: str) -> float:
    weighted = row.get(weighted_key)
    if weighted is not None:
        return float(weighted or 0.0)
    return float(row.get(fallback_key, 0.0) or 0.0)


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
        return (
            f"Selected because similar prior work shows {name or 'this value stream'} "
            f"recurring in the same business pattern: {analog}"
        )
    return fallback


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


def _select_llm_candidates(
    merged: List[dict],
    llm_candidate_pool: Dict[str, List[dict]],
    *,
    max_llm_candidates: int,
) -> List[dict]:
    selected_keys: set[str] = set()
    selected: List[dict] = []

    def select_row(row: dict, reason: str) -> None:
        key = _norm_name(str(row.get("entity_name") or ""))
        if not key or key in selected_keys:
            return
        selected_keys.add(key)
        row["candidate_status"] = "sent_to_llm"
        row["candidate_status_reason"] = reason
        selected.append(row)

    lane_quotas = _lane_quotas(max_llm_candidates)
    lane_reasons = {
        "confirmed_direct": "protected_confirmed_lane",
        "historical_recall": "protected_historical_lane",
        "semantic_direct": "protected_semantic_lane",
    }

    ordered_pool = {
        lane: sorted(rows, key=_lane_priority, reverse=True)
        for lane, rows in llm_candidate_pool.items()
    }

    for lane in ("confirmed_direct", "historical_recall", "semantic_direct"):
        quota = min(len(ordered_pool.get(lane, [])), lane_quotas.get(lane, 0))
        for row in ordered_pool.get(lane, [])[:quota]:
            if len(selected) >= max_llm_candidates:
                break
            select_row(row, lane_reasons[lane])

    if len(selected) < max_llm_candidates:
        pending_keys = {
            _norm_name(str(row.get("entity_name") or ""))
            for lane_rows in llm_candidate_pool.values()
            for row in lane_rows
        }
        overflow = sorted(
            [
                row
                for lane_rows in ordered_pool.values()
                for row in lane_rows
                if _norm_name(str(row.get("entity_name") or "")) in pending_keys
            ],
            key=_overflow_priority,
            reverse=True,
        )
        for row in overflow:
            if len(selected) >= max_llm_candidates:
                break
            key = _norm_name(str(row.get("entity_name") or ""))
            if key in selected_keys or key not in pending_keys:
                continue
            select_row(row, "within_llm_candidate_cap")

    for lane_rows in llm_candidate_pool.values():
        for row in lane_rows:
            key = _norm_name(str(row.get("entity_name") or ""))
            if key in selected_keys:
                continue
            row["candidate_status"] = "dropped_before_llm"
            row["candidate_status_reason"] = "llm_candidate_cap"

    return selected


def _lane_quotas(total: int) -> Dict[str, int]:
    if total <= 0:
        return {
            "confirmed_direct": 0,
            "historical_recall": 0,
            "semantic_direct": 0,
        }

    confirmed = min(max(1, math.ceil(total * 0.55)), 32)
    historical = min(max(1, math.ceil(total * 0.30)), 18)

    while confirmed + historical > total:
        if confirmed > historical and confirmed > 1:
            confirmed -= 1
        elif historical > 1:
            historical -= 1
        else:
            break

    semantic = max(0, total - confirmed - historical)
    if total >= 3 and semantic == 0:
        if confirmed > historical and confirmed > 1:
            confirmed -= 1
        elif historical > 1:
            historical -= 1
        semantic = 1

    return {
        "confirmed_direct": confirmed,
        "historical_recall": historical,
        "semantic_direct": semantic,
    }


def _lane_priority(row: dict) -> tuple:
    lane = str(row.get("candidate_lane") or "")
    if lane == "historical_recall":
        return _historical_lane_priority(row)
    if lane == "confirmed_direct":
        return _confirmed_lane_priority(row)
    if lane == "semantic_direct":
        return _semantic_lane_priority(row)
    return (0.0,)


def _historical_lane_priority(row: dict) -> tuple:
    ticket_count = len(
        {
            str(ticket_id).strip().lower()
            for ticket_id in (row.get("supporting_ticket_ids") or [])
            if str(ticket_id).strip()
        }
    )
    return (
        ticket_count,
        int(row.get("support_count", 0) or 0),
        int(row.get("direct_count", 0) or 0),
        float(row.get("best_support_score", 0.0) or 0.0),
        float(row.get("avg_support_score", 0.0) or 0.0),
        float(row.get("weighted_support_count", 0.0) or 0.0),
        float(row.get("historical_strength", 0.0) or 0.0),
        float(row.get("ranking_score", 0.0) or 0.0),
    )


def _confirmed_lane_priority(row: dict) -> tuple:
    return (
        float(row.get("ranking_score", 0.0) or 0.0),
        float(row.get("semantic_score", 0.0) or 0.0),
        int(row.get("support_count", 0) or 0),
        float(row.get("best_support_score", 0.0) or 0.0),
    )


def _semantic_lane_priority(row: dict) -> tuple:
    return (
        float(row.get("semantic_score", 0.0) or 0.0),
        float(row.get("ranking_score", 0.0) or 0.0),
    )


def _overflow_priority(row: dict) -> tuple:
    lane = str(row.get("candidate_lane") or "")
    lane_weight = {
        "confirmed_direct": 3,
        "historical_recall": 2,
        "semantic_direct": 1,
    }.get(lane, 0)
    return (lane_weight, *_lane_priority(row))
