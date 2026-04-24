from __future__ import annotations

import logging
from typing import Iterable, List

from .prompt_builder import build_candidate_prompt, build_system_prompt

logger = logging.getLogger(__name__)


def generate_value_streams(
    query_for_prompt: str,
    llm_candidates: List[dict],
    auto_selected: List[dict] | None = None,
) -> dict:
    from vs_app.integrations.clients.generation_service import GenerationService

    from ..ranking.reranker import sanitize_selected

    auto_selected = list(auto_selected or [])
    if not llm_candidates:
        return {
            "selected_value_streams": _dedupe_selected(auto_selected),
            "llm_selected_value_streams": [],
            "raw_response": None,
            "candidates_used": [],
        }

    gen_svc = GenerationService()
    prompt = build_candidate_prompt(
        query_for_prompt=query_for_prompt,
        candidates=llm_candidates,
    )
    system_prompt = build_system_prompt()

    from vs_app.modules.prompts.loader import SelectionResult

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
