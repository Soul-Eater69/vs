from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import List

logger = logging.getLogger(__name__)


def sanitize_selected(parsed: dict, candidates: List[dict]) -> dict:
    """Match LLM-selected VS names back to valid candidates."""
    logger.info(
        "[SANITIZE] %d selections vs %d candidates",
        len(parsed.get("selected_value_streams") or []),
        len(candidates),
    )

    by_name = {candidate.get("entity_name", "").strip().lower(): candidate for candidate in candidates if candidate.get("entity_name")}
    by_id = {
        str(candidate.get("entity_id") or "").strip().lower(): candidate
        for candidate in candidates
        if str(candidate.get("entity_id") or "").strip()
    }

    def _norm(text: str) -> str:
        text = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower().strip())
        return re.sub(r"\s+", " ", text).strip()

    by_norm = {_norm(candidate["entity_name"]): candidate for candidate in candidates if candidate.get("entity_name")}

    selected: List[dict] = []
    for row in parsed.get("selected_value_streams") or []:
        raw_name = str(row.get("entity_name") or row.get("name") or "").strip()
        raw_id = str(row.get("entity_id") or "").strip().lower()

        if not raw_name:
            continue

        candidate = None
        method = "none"

        if raw_id and raw_id in by_id:
            candidate = by_id[raw_id]
            method = "entity_id"

        if candidate is None and raw_name.lower() in by_name:
            candidate = by_name[raw_name.lower()]
            method = "exact_name"

        if candidate is None and _norm(raw_name) in by_norm:
            candidate = by_norm[_norm(raw_name)]
            method = "normalized"

        if candidate is None:
            best = None
            best_score = 0.0
            for existing_candidate in candidates:
                candidate_name = str(existing_candidate.get("entity_name") or "").strip()
                if not candidate_name:
                    continue

                score = SequenceMatcher(None, _norm(raw_name), _norm(candidate_name)).ratio()
                if score > best_score:
                    best_score = score
                    best = existing_candidate

            if best and best_score >= 0.75:
                candidate = best
                method = f"fuzzy({best_score:.2f})"

        if candidate is None:
            logger.warning("[SANITIZE] DROPPED: '%s'", raw_name)
            continue

        logger.info(
            "[SANITIZE] KEPT: '%s' -> '%s' via %s",
            raw_name,
            candidate.get("entity_name", "?"),
            method,
        )

        selected.append(
            {
                "entity_id": row.get("entity_id") or candidate.get("entity_id", ""),
                "entity_name": candidate.get("entity_name", raw_name),
                "confidence": float(row.get("confidence", 0.0) or 0.0),
                "reason": str(row.get("reason") or ""),
                "supporting_ticket_ids": row.get("supporting_ticket_ids") or [],
                "supporting_chunk_ids": row.get("supporting_chunk_ids") or [],
            }
        )

    parsed["selected_value_streams"] = selected
    parsed["rejected_candidates"] = parsed.get("rejected_candidates") or []

    logger.info(
        "[SANITIZE] Result: %d kept, %d rejected",
        len(selected),
        len(parsed["rejected_candidates"]),
    )

    return parsed
