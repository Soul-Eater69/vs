from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint_names(rows: list[dict]) -> str:
    names = [str(row.get("entity_name") or "").strip() for row in rows if str(row.get("entity_name") or "").strip()]
    return fingerprint(names)


def build_rag_debug_fingerprints(
    *,
    cleaned_query: str,
    query_for_prompt: str,
    semantic_candidates: list[dict],
    historical_support: list[dict],
    merged_candidates: list[dict],
    llm_candidates: list[dict],
    llm_selected: list[dict],
    final_selected: list[dict],
) -> dict[str, Any]:
    return {
        "fingerprints": {
            "cleaned_query": fingerprint(cleaned_query),
            "query_for_prompt": fingerprint(query_for_prompt),
            "semantic_candidates": fingerprint_names(semantic_candidates),
            "historical_support": fingerprint_names(historical_support),
            "merged_candidates": fingerprint_names(merged_candidates),
            "llm_candidates": fingerprint_names(llm_candidates),
            "llm_selected": fingerprint_names(llm_selected),
            "final_selected": fingerprint_names(final_selected),
        },
        "counts": {
            "semantic_candidates": len(semantic_candidates),
            "historical_support": len(historical_support),
            "merged_candidates": len(merged_candidates),
            "llm_candidates": len(llm_candidates),
            "llm_selected": len(llm_selected),
            "final_selected": len(final_selected),
        },
    }
