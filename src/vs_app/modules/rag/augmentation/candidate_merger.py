from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateWindowPolicy:
    max_semantic_plus_historical: int = 8
    max_semantic_only: int = 12
    max_historical_only: int = 10
    max_supporting_tickets_per_candidate: int = 3

    @property
    def max_llm_candidates(self) -> int:
        return (
            self.max_semantic_plus_historical
            + self.max_semantic_only
            + self.max_historical_only
        )


def merge_candidate_sources(
    semantic_candidates: list[dict],
    historical_support: list[dict],
    *,
    policy: CandidateWindowPolicy | None = None,
    max_llm_candidates: int | None = None,
    **legacy_kwargs,
) -> dict:
    """Merge semantic and historical evidence, then choose a bounded LLM window.

    This function intentionally does not decide value-stream truth. It only organizes
    evidence into lanes and controls prompt size.
    """

    if legacy_kwargs:
        logger.debug("Ignoring legacy candidate threshold kwargs: %s", sorted(legacy_kwargs))

    active_policy = policy or CandidateWindowPolicy()
    if max_llm_candidates is not None and max_llm_candidates < active_policy.max_llm_candidates:
        active_policy = _policy_with_total_cap(active_policy, max_llm_candidates)

    by_name: dict[str, dict] = {}

    for semantic_rank, row in enumerate(semantic_candidates, start=1):
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        key = _norm_name(name)
        by_name[key] = _base_candidate(
            entity_id=str(row.get("entity_id") or "").strip(),
            entity_name=name,
            description=str(row.get("description") or "").strip(),
        )
        by_name[key].update(
            {
                "from_semantic": True,
                "semantic_score": _float(row.get("semantic_score")),
                "semantic_rank": semantic_rank,
            }
        )

    for row in historical_support:
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue
        key = _norm_name(name)
        existing = by_name.setdefault(
            key,
            _base_candidate(
                entity_id=str(row.get("entity_id") or "").strip(),
                entity_name=name,
                description=str(row.get("description") or "").strip(),
            ),
        )
        if not existing.get("entity_id"):
            existing["entity_id"] = str(row.get("entity_id") or "").strip()
        if not existing.get("description"):
            existing["description"] = str(row.get("description") or "").strip()

        supporting_ticket_ids = _unique_text(row.get("supporting_ticket_ids") or [])
        supporting_ticket_count = (
            len(supporting_ticket_ids)
            if supporting_ticket_ids
            else int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0)
        )
        weighted_support = _weighted_support(row, supporting_ticket_count)

        existing.update(
            {
                "from_historical": True,
                "supporting_ticket_count": supporting_ticket_count,
                "support_count": supporting_ticket_count,
                "direct_count": int(row.get("direct_count", 0) or 0),
                "implied_count": int(row.get("implied_count", 0) or 0),
                "best_support_score": _float(row.get("best_support_score")),
                "avg_support_score": _float(row.get("avg_support_score")),
                "weighted_support": weighted_support,
                "weighted_support_count": weighted_support,
                "weighted_direct_count": _float(row.get("weighted_direct_count")),
                "weighted_implied_count": _float(row.get("weighted_implied_count")),
                "supporting_ticket_ids": supporting_ticket_ids[
                    : active_policy.max_supporting_tickets_per_candidate
                ],
                "supporting_chunk_ids": _unique_text(row.get("supporting_chunk_ids") or [])[
                    : active_policy.max_supporting_tickets_per_candidate
                ],
                "historical_reasons": _unique_text(row.get("historical_reasons") or [])[
                    : active_policy.max_supporting_tickets_per_candidate
                ],
                "label_sources": _unique_text(row.get("label_sources") or []),
            }
        )

    merged = list(by_name.values())
    for row in merged:
        lane = assign_lane(row)
        row["lane"] = lane
        row["bucket"] = lane
        row["candidate_lane"] = lane
        row["ranking_score"] = 0.0
        row["historical_strength"] = 0.0

    semantic_plus = sorted(
        [row for row in merged if row["lane"] == "semantic_plus_historical"],
        key=_sort_semantic_plus_historical,
    )[: active_policy.max_semantic_plus_historical]
    semantic_only = sorted(
        [row for row in merged if row["lane"] == "semantic_only"],
        key=_sort_semantic_only,
    )[: active_policy.max_semantic_only]
    historical_only = sorted(
        [row for row in merged if row["lane"] == "historical_only"],
        key=_sort_historical_only,
    )[: active_policy.max_historical_only]

    llm_candidates = semantic_plus + semantic_only + historical_only
    llm_keys = {_norm_name(str(row.get("entity_name") or "")) for row in llm_candidates}

    for row in merged:
        if _norm_name(str(row.get("entity_name") or "")) in llm_keys:
            row["candidate_status"] = "sent_to_llm"
            row["candidate_status_reason"] = "within_candidate_window"
        else:
            row["candidate_status"] = "outside_llm_window"
            row["candidate_status_reason"] = "lane_window_cap"

    merged.sort(key=_sort_merged_for_debug)

    logger.info(
        "[HIST-RAG] %d merged candidates -> 0 auto-selected, %d for LLM",
        len(merged),
        len(llm_candidates),
    )

    return {
        "merged_candidates": merged,
        "auto_selected_value_streams": [],
        "llm_candidates": llm_candidates,
        "candidate_window_policy": {
            **asdict(active_policy),
            "max_llm_candidates": active_policy.max_llm_candidates,
        },
        "candidate_window_counts": {
            "semantic_plus_historical": len(semantic_plus),
            "semantic_only": len(semantic_only),
            "historical_only": len(historical_only),
        },
    }


def assign_lane(row: dict) -> str:
    if row.get("from_semantic") and row.get("from_historical"):
        return "semantic_plus_historical"
    if row.get("from_semantic"):
        return "semantic_only"
    if row.get("from_historical"):
        return "historical_only"
    return "unknown"


def historical_support_weight(score: float) -> float:
    if score >= 0.80:
        return 1.0
    if score >= 0.70:
        return 0.6
    if score >= 0.60:
        return 0.3
    return 0.0


def _base_candidate(*, entity_id: str, entity_name: str, description: str) -> dict:
    return {
        "entity_id": entity_id,
        "entity_name": entity_name,
        "description": description,
        "lane": "unknown",
        "bucket": "unknown",
        "candidate_lane": "unknown",
        "from_semantic": False,
        "semantic_score": 0.0,
        "semantic_rank": None,
        "from_historical": False,
        "supporting_ticket_count": 0,
        "support_count": 0,
        "direct_count": 0,
        "implied_count": 0,
        "best_support_score": 0.0,
        "avg_support_score": 0.0,
        "weighted_support": 0.0,
        "weighted_support_count": 0.0,
        "weighted_direct_count": 0.0,
        "weighted_implied_count": 0.0,
        "supporting_ticket_ids": [],
        "supporting_chunk_ids": [],
        "historical_reasons": [],
        "label_sources": [],
        "ranking_score": 0.0,
        "historical_strength": 0.0,
        "candidate_status": "outside_llm_window",
        "candidate_status_reason": "lane_window_cap",
    }


def _policy_with_total_cap(policy: CandidateWindowPolicy, total: int) -> CandidateWindowPolicy:
    total = max(0, int(total or 0))
    if total >= policy.max_llm_candidates:
        return policy

    semantic_plus = min(policy.max_semantic_plus_historical, total)
    remaining = total - semantic_plus
    historical_only = min(policy.max_historical_only, max(0, remaining // 2))
    semantic_only = max(0, remaining - historical_only)
    if semantic_only > policy.max_semantic_only:
        overflow = semantic_only - policy.max_semantic_only
        semantic_only = policy.max_semantic_only
        historical_only = min(policy.max_historical_only, historical_only + overflow)

    return CandidateWindowPolicy(
        max_semantic_plus_historical=semantic_plus,
        max_semantic_only=semantic_only,
        max_historical_only=historical_only,
        max_supporting_tickets_per_candidate=policy.max_supporting_tickets_per_candidate,
    )


def _sort_semantic_plus_historical(row: dict) -> tuple:
    return (
        -_float(row.get("semantic_score")),
        -_float(row.get("best_support_score")),
        -_float(row.get("weighted_support", row.get("weighted_support_count"))),
        -int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0),
        str(row.get("entity_name") or "").lower(),
    )


def _sort_semantic_only(row: dict) -> tuple:
    return (
        -_float(row.get("semantic_score")),
        str(row.get("entity_name") or "").lower(),
    )


def _sort_historical_only(row: dict) -> tuple:
    return (
        -_float(row.get("best_support_score")),
        -_float(row.get("weighted_support", row.get("weighted_support_count"))),
        -int(row.get("direct_count", 0) or 0),
        -int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0),
        -int(row.get("implied_count", 0) or 0),
        -_float(row.get("avg_support_score")),
        str(row.get("entity_name") or "").lower(),
    )


def _sort_merged_for_debug(row: dict) -> tuple:
    lane = str(row.get("lane") or "")
    if lane == "semantic_plus_historical":
        lane_key = 0
        detail_key = _sort_semantic_plus_historical(row)
    elif lane == "semantic_only":
        lane_key = 1
        detail_key = _sort_semantic_only(row)
    elif lane == "historical_only":
        lane_key = 2
        detail_key = _sort_historical_only(row)
    else:
        lane_key = 9
        detail_key = (str(row.get("entity_name") or "").lower(),)
    return (lane_key, *detail_key)


def _weighted_support(row: dict, supporting_ticket_count: int) -> float:
    if row.get("weighted_support") is not None:
        return _float(row.get("weighted_support"))
    if row.get("weighted_support_count") is not None:
        return _float(row.get("weighted_support_count"))
    return round(historical_support_weight(_float(row.get("best_support_score"))) * supporting_ticket_count, 4)


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _norm_name(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _unique_text(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
