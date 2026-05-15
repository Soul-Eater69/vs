from __future__ import annotations

from dataclasses import asdict, dataclass
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


# Streams that historically show up as false positives because they have broad
# wording overlap with many idea cards (analytics, governance, generic care, claim
# adjudication, prescription fulfillment, etc.). We don't ban them — strong evidence
# can still surface them — but we apply a sort penalty so they don't crowd out
# stream-specific candidates in the lane caps.
GENERIC_OR_RISKY_STREAMS = {
    "discover business insights",
    "promote community health",
    "administer quality management program",
    "receive care",
    "adjudicate claim",
    "fill and manage prescriptions",
    "manage producer operations",
    "align and execute it strategy",
    "develop mission, vision, and strategy",
}

MIN_HISTORICAL_CANDIDATE_SCORE = 0.08


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

    # Evidence-qualified selection: gate historical-only and semantic-only candidates
    # behind quality floors, then fill the LLM window in priority order:
    #   1. all merged (semantic+historical) candidates
    #   2. evidence-qualified historical-only candidates
    #   3. very strong semantic-only candidates
    # Stops at llm_candidate_window. This prevents weak semantic-only candidates from
    # ever crowding out merged candidates, and prevents thin historical-only
    # candidates from entering the prompt at all.
    semantic_plus_all = sorted(
        [row for row in merged if row["lane"] == "semantic_plus_historical"],
        key=_sort_semantic_plus_historical,
    )
    historical_only_all = sorted(
        [
            row
            for row in merged
            if row["lane"] == "historical_only" and _is_good_historical_only(row)
        ],
        key=_sort_historical_only,
    )
    semantic_only_all = sorted(
        [
            row
            for row in merged
            if row["lane"] == "semantic_only" and _is_strong_semantic_only(row)
        ],
        key=_sort_semantic_only,
    )

    llm_limit = max_llm_candidates or active_policy.max_llm_candidates

    llm_candidates: list[dict] = []
    for row in semantic_plus_all[: active_policy.max_semantic_plus_historical]:
        if len(llm_candidates) >= llm_limit:
            break
        llm_candidates.append(row)

    historical_room = min(
        active_policy.max_historical_only, max(0, llm_limit - len(llm_candidates))
    )
    for row in historical_only_all[:historical_room]:
        if len(llm_candidates) >= llm_limit:
            break
        llm_candidates.append(row)

    semantic_room = min(
        active_policy.max_semantic_only, max(0, llm_limit - len(llm_candidates))
    )
    for row in semantic_only_all[:semantic_room]:
        if len(llm_candidates) >= llm_limit:
            break
        llm_candidates.append(row)

    semantic_plus = [row for row in llm_candidates if row["lane"] == "semantic_plus_historical"]
    historical_only = [row for row in llm_candidates if row["lane"] == "historical_only"]
    semantic_only = [row for row in llm_candidates if row["lane"] == "semantic_only"]
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


def _is_good_historical_only(row: dict) -> bool:
    """Gate historical-only candidates behind the retriever evidence floor."""
    hits = int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0)
    best = _float(row.get("best_support_score"))
    direct = int(row.get("direct_count", 0) or 0)
    weighted = _float(row.get("weighted_support", row.get("weighted_support_count")))
    if best < MIN_HISTORICAL_CANDIDATE_SCORE:
        return False
    return hits >= 2 or direct >= 1 or weighted >= 0.6


def _is_strong_semantic_only(row: dict) -> bool:
    """Gate semantic-only candidates behind a higher semantic floor than the merged
    lane. Generic/risky streams need an even stronger floor since they're prone to
    false-positive selection.
    """
    name = _norm_name(str(row.get("entity_name") or ""))
    semantic = _float(row.get("semantic_score"))
    if name in GENERIC_OR_RISKY_STREAMS:
        return semantic >= 1.35
    return semantic >= 1.20


def _sort_semantic_plus_historical(row: dict) -> tuple:
    # Blend semantic + historical signals so a candidate with strong historical evidence
    # isn't buried under a marginally-better-semantic one that has only 1 hit. The whole
    # point of this lane is "best of both" — the sort should reflect that.
    semantic = _float(row.get("semantic_score"))
    hits = int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0)
    best_support = _float(row.get("best_support_score"))
    # Saturate at 10 hits (diminishing returns) and scale to roughly the same magnitude
    # as semantic_score so a 10+ hit candidate gets a meaningful boost without dominating.
    historical_boost = min(1.0, hits / 10.0) * 0.20 + best_support * 0.15
    blended = semantic + historical_boost
    # Small penalty for known-generic streams when their historical evidence is weak.
    if _norm_name(str(row.get("entity_name") or "")) in GENERIC_OR_RISKY_STREAMS and hits < 3:
        blended -= 0.20
    return (
        -blended,
        -semantic,
        -best_support,
        -_float(row.get("weighted_support", row.get("weighted_support_count"))),
        -hits,
        str(row.get("entity_name") or "").lower(),
    )


def _sort_semantic_only(row: dict) -> tuple:
    name = _norm_name(str(row.get("entity_name") or ""))
    penalty = 0.25 if name in GENERIC_OR_RISKY_STREAMS else 0.0
    semantic = _float(row.get("semantic_score")) - penalty
    return (
        -semantic,
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
