from __future__ import annotations

import re
from typing import List

from vs_app.modules.prompts.loader import (
    build_historical_selection_prompt,
    build_historical_selection_system_prompt,
)


def build_system_prompt() -> str:
    return build_historical_selection_system_prompt(
        min_select=4,
        max_select=12,
    )


def build_candidate_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    ordered_candidates = _order_candidates_for_llm(candidates)
    blocks = []
    for idx, row in enumerate(ordered_candidates, start=1):
        bucket = str(row.get("bucket") or "").strip()
        lane = str(row.get("candidate_lane") or "").strip()
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Evidence bucket: {bucket or 'unknown'}",
        ]
        if lane:
            lines.append(f"Candidate lane: {lane}")

        description = str(row.get("description") or "").strip()
        if description:
            lines.append(f"Description: {description[:320]}")

        lane_guidance = _lane_guidance(lane)
        if lane_guidance:
            lines.append(f"Lane guidance: {lane_guidance}")

        if row.get("from_semantic"):
            lines.append(f"Semantic score: {float(row.get('semantic_score', 0.0) or 0.0):.4f}")

        if row.get("from_historical"):
            direct_count = int(row.get("direct_count", 0) or 0)
            implied_count = int(row.get("implied_count", 0) or 0)
            lines.append(
                "Historical support: "
                f"{int(row.get('support_count', 0) or 0)} tickets "
                f"({direct_count} direct, {implied_count} implied), "
                f"best similarity {float(row.get('best_support_score', 0.0) or 0.0):.4f}, "
                f"average similarity {float(row.get('avg_support_score', 0.0) or 0.0):.4f}"
            )
            if lane == "historical_recall":
                lines.append(
                    "Historical role: implied gap-fill candidate. Semantic retrieval likely missed this,"
                    " so judge it by whether the analog pattern suggests an unstated but materially"
                    " relevant value stream."
                )

        overlaps = _overlap_candidates(row, ordered_candidates)
        if overlaps:
            lines.append(f"Potential overlap/conflict: {', '.join(overlaps[:3])}")

        reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
        if reasons:
            direct_reasons = [reason for reason in reasons if "/ direct]" in reason]
            implied_reasons = [reason for reason in reasons if "/ implied]" in reason]
            ordered = (direct_reasons + implied_reasons)[:3]
            lines.append("Analog evidence:")
            for reason in ordered:
                lines.append(f"  - {reason}")

        blocks.append("\n".join(lines))

    return build_historical_selection_prompt(
        query_for_prompt=query_for_prompt,
        candidate_blocks="\n\n".join(blocks),
    )


def _order_candidates_for_llm(candidates: List[dict]) -> List[dict]:
    lane_priority = {
        "confirmed_direct": 0,
        "historical_recall": 1,
        "semantic_direct": 2,
    }
    return sorted(
        list(candidates),
        key=lambda row: (
            lane_priority.get(str(row.get("candidate_lane") or ""), 9),
            -float(row.get("ranking_score", 0.0) or 0.0),
        ),
    )


def _lane_guidance(lane: str) -> str:
    if lane == "confirmed_direct":
        return "Strong direct candidate with both semantic and historical support."
    if lane == "historical_recall":
        return (
            "Gap-fill lane. This candidate may be materially relevant even without explicit wording"
            " overlap if repeated analog evidence is coherent."
        )
    if lane == "semantic_direct":
        return "Direct semantic lane. Keep only if the business fit is genuinely material."
    return ""


def _overlap_candidates(row: dict, candidates: List[dict]) -> List[str]:
    current_name = str(row.get("entity_name") or "").strip()
    current_tokens = _name_tokens(current_name)
    overlaps: List[str] = []
    for other in candidates:
        other_name = str(other.get("entity_name") or "").strip()
        if not other_name or other_name == current_name:
            continue
        other_tokens = _name_tokens(other_name)
        shared = current_tokens & other_tokens
        if len(shared) >= 2:
            overlaps.append(other_name)
    return overlaps


def _name_tokens(name: str) -> set[str]:
    stop_words = {"and", "for", "the", "to", "of", "in"}
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", (name or "").lower())
        if token and token not in stop_words
    }
    return tokens
