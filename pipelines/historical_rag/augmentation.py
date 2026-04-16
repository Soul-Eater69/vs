from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def merge_candidate_sources(
    semantic_candidates: List[dict],
    historical_support: List[dict],
    *,
    max_llm_candidates: int = 16,
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
        key=lambda row: (
            1 if row.get("from_semantic") else 0,
            float(row.get("historical_strength", 0.0)),
            float(row.get("semantic_score", 0.0)),
            float(row.get("best_support_score", 0.0)),
        ),
        reverse=True,
    )

    auto_selected: List[dict] = []
    llm_candidates: List[dict] = []

    for row in merged:
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
        "[HIST-RAG] %d merged candidates -> %d auto, %d for LLM",
        len(merged),
        len(auto_selected),
        len(llm_candidates),
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
        + 0.12 * int(row.get("direct_count", 0) or 0)
        + 0.05 * int(row.get("implied_count", 0) or 0)
        + _label_source_adjustment(row)
    )


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
    if int(row.get("support_count", 0) or 0) < support_count:
        return False
    if float(row.get("best_support_score", 0.0) or 0.0) < min_score:
        return False
    return int(row.get("direct_count", 0) or 0) >= int(row.get("implied_count", 0) or 0)


def _should_send_to_llm(row: dict, *, support_count: int, min_score: float) -> bool:
    if int(row.get("support_count", 0) or 0) >= support_count:
        return True
    return float(row.get("best_support_score", 0.0) or 0.0) >= min_score


def _to_selected_value_stream(row: dict) -> dict:
    support_count = int(row.get("support_count", 0) or 0)
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    best_score = float(row.get("best_support_score", 0.0) or 0.0)
    confidence = min(0.95, 0.45 + 0.07 * support_count + 0.10 * min(best_score, 1.0))

    reason = (
        f"Recovered from {support_count} similar historical tickets "
        f"({direct_count} direct, {implied_count} implied)."
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
