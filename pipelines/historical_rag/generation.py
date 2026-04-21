from __future__ import annotations

import logging
from typing import Iterable, List

from ...clients.generation_service import GenerationService
from ...prompts import (
    SelectionResult,
    build_historical_selection_prompt,
    build_historical_selection_system_prompt,
)
from ..retrieval.pipeline import sanitize_selected

logger = logging.getLogger(__name__)


def generate_value_streams(
    query_for_prompt: str,
    llm_candidates: List[dict],
    auto_selected: List[dict] | None = None,
) -> dict:
    auto_selected = list(auto_selected or [])
    if not llm_candidates:
        return {
            "selected_value_streams": _dedupe_selected(auto_selected),
            "llm_selected_value_streams": [],
            "raw_response": None,
            "candidates_used": [],
        }

    gen_svc = GenerationService()
    prompt = _build_prompt(
        query_for_prompt=query_for_prompt,
        candidates=llm_candidates,
    )
    system_prompt = _build_system_prompt()

    result = gen_svc.generate_structured(
        query=prompt,
        output_schema=SelectionResult,
        system_prompt=system_prompt,
    )
    parsed = result.model_dump() if hasattr(result, "model_dump") else {"selected_value_streams": []}
    parsed = sanitize_selected(parsed, llm_candidates)

    llm_selected = parsed.get("selected_value_streams", [])
    final_selected = _merge_selected(auto_selected, llm_selected)

    return {
        "selected_value_streams": final_selected,
        "llm_selected_value_streams": llm_selected,
        "raw_response": parsed,
        "candidates_used": llm_candidates,
    }


def _build_system_prompt() -> str:
    return build_historical_selection_system_prompt(
        min_select=6,
        max_select=12,
    )


def _build_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    blocks = []
    for idx, row in enumerate(candidates, start=1):
        bucket = str(row.get("bucket") or "").strip()
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Evidence bucket: {bucket or 'unknown'}",
        ]

        description = str(row.get("description") or "").strip()
        if description:
            lines.append(f"Description: {description[:320]}")

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

            reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
            if reasons:
                # Show direct-tagged analogs first so the LLM sees the strongest
                # evidence before implied ones.
                direct_reasons = [r for r in reasons if "/ direct]" in r]
                implied_reasons = [r for r in reasons if "/ implied]" in r]
                ordered = (direct_reasons + implied_reasons)[:3]
                lines.append("Analog evidence:")
                for reason in ordered:
                    lines.append(f"  - {reason}")

        blocks.append("\n".join(lines))

    return build_historical_selection_prompt(
        query_for_prompt=query_for_prompt,
        candidate_blocks="\n\n".join(blocks),
    )


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
