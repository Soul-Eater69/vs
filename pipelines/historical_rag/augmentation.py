from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def merge_candidate_sources(
    semantic_candidates: List[dict],
    historical_support: List[dict],
    *,
    max_llm_candidates: int = 24,
    strong_support_count: int = 3,
    strong_support_score: float = 0.60,
    moderate_support_count: int = 2,
    moderate_support_score: float = 0.45,
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

    merged.sort(
        key=lambda row: _ranking_score(row),
        reverse=True,
    )

    auto_selected: List[dict] = []
    llm_candidates: List[dict] = []

    for row in merged:
        if _should_auto_include_merged(row, min_score=strong_support_score):
            auto_selected.append(_to_selected_merged(row))
            continue

        if _should_auto_include(
            row,
            support_count=strong_support_count,
            min_score=strong_support_score,
        ):
            auto_selected.append(_to_selected_value_stream(row))
            continue

        if row.get("from_semantic") or _should_send_to_llm(
            row,
            support_count=moderate_support_count,
            min_score=moderate_support_score,
        ):
            llm_candidates.append(row)

    llm_candidates = llm_candidates[:max_llm_candidates]
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


def _ranking_score(row: dict) -> float:
    """Blended rank score so strong historical-only candidates interleave with
    weak semantic ones rather than always being pushed to the end of the list."""
    semantic = float(row.get("semantic_score", 0.0) or 0.0)
    hist = float(row.get("historical_strength", 0.0) or 0.0)
    if row.get("from_semantic") and row.get("from_historical"):
        # Merged: semantic dominates but historical adds a boost
        return semantic + 0.25 * hist
    if row.get("from_semantic"):
        return semantic
    # Historical-only: project onto same scale as semantic scores so a strong
    # historical candidate (hist ~0.85) can outrank a weak semantic one (~0.50).
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


def _should_auto_include(row: dict, *, support_count: int, min_score: float) -> bool:
    if row.get("from_semantic"):
        return False
    if float(row.get("best_support_score", 0.0) or 0.0) < min_score:
        return False

    direct_count = int(row.get("direct_count", 0) or 0)
    total_count = int(row.get("support_count", 0) or 0)

    # Direct-count shortcut: multiple direct-tagged analogs at good similarity
    # bypasses weighted-count check because per-ticket weight is diluted when
    # a ticket has many VS attached — the raw direct count is more reliable signal.
    if direct_count >= 2 and total_count >= support_count:
        return True

    if total_count < support_count:
        return False
    if _weighted_support_value(row, "weighted_support_count", "support_count") < max(1.5, support_count * 0.45):
        return False
    return _weighted_support_value(row, "weighted_direct_count", "direct_count") >= _weighted_support_value(
        row,
        "weighted_implied_count",
        "implied_count",
    )


def _should_auto_include_merged(row: dict, *, min_score: float) -> bool:
    """Auto-select candidates with both strong semantic AND strong historical support.

    These are the safest bets — two independent signals agree. Bypassing the LLM
    cap prevents high-scoring merged candidates from being crowded out by false
    positives that the LLM over-selects from semantic-only evidence.

    Semantic score threshold of 1.0 targets reranker scores (range 0-3); plain
    vector fallback scores top out at 1.0 so this path rarely fires on fallback,
    which is acceptable since fallback retrieval is already degraded.
    """
    if not (row.get("from_semantic") and row.get("from_historical")):
        return False
    if float(row.get("semantic_score", 0.0) or 0.0) < 1.0:
        return False
    if float(row.get("best_support_score", 0.0) or 0.0) < min_score:
        return False
    if int(row.get("support_count", 0) or 0) < 4:
        return False
    return True


def _to_selected_merged(row: dict) -> dict:
    semantic_score = float(row.get("semantic_score", 0.0) or 0.0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    support_count = int(row.get("support_count", 0) or 0)
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")
    confidence = min(0.95, 0.55 + 0.10 * min(semantic_score, 2.0) + 0.08 * min(weighted_support, 3.0))

    reason = (
        f"Strong semantic match (score {semantic_score:.3f}) confirmed by "
        f"{support_count} historical tickets ({direct_count} direct, {implied_count} implied; "
        f"best similarity {best_score:.3f})."
    )
    example_reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
    if example_reasons:
        reason += f" Example: {example_reasons[0]}"

    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "confidence": round(confidence, 4),
        "reason": reason,
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or [])[:5],
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or [])[:5],
    }


def _should_send_to_llm(row: dict, *, support_count: int, min_score: float) -> bool:
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")
    if int(row.get("support_count", 0) or 0) >= support_count:
        return weighted_support >= max(1.0, support_count * 0.4)
    return (
        float(row.get("best_support_score", 0.0) or 0.0) >= min_score
        and weighted_support >= 0.5
    )


def _to_selected_value_stream(row: dict) -> dict:
    support_count = int(row.get("support_count", 0) or 0)
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    weighted_support = _weighted_support_value(row, "weighted_support_count", "support_count")
    confidence = min(0.92, 0.38 + 0.12 * min(weighted_support, 4.0) + 0.10 * min(best_score, 1.0))

    reason = (
        f"Recovered from {support_count} similar historical tickets "
        f"({direct_count} direct, {implied_count} implied; weighted support {weighted_support:.2f})."
    )
    example_reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
    if example_reasons:
        reason += f" Example: {example_reasons[0]}"

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
