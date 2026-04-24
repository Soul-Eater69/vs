from __future__ import annotations


def _score_candidate(row: dict) -> float:
    return float(
        row.get("score")
        or row.get("_aggregated_best_score")
        or row.get("semantic_score")
        or row.get("historical_strength")
        or row.get("best_support_score")
        or row.get("avg_support_score")
        or row.get("@search.reranker_score")
        or row.get("@search.score")
        or 0.0
    )


def _normalize_candidate_rows(rows: list[dict] | None, source: str) -> list[dict]:
    normalized: list[dict] = []
    for raw_row in rows or []:
        row = dict(raw_row or {})
        score = _score_candidate(row)
        row.setdefault("score", score)
        row.setdefault("_aggregated_best_score", score)

        support_count = int(row.get("support_count") or row.get("_support_count") or 0)
        if support_count:
            row.setdefault("_support_count", support_count)

        row.setdefault("candidate_source", source)
        normalized.append(row)
    return normalized


def normalize_for_ui(result: dict) -> dict:
    if "candidates_used" not in result:
        candidates = []
        for row in result.get("candidate_value_streams", []) or []:
            score = float(
                row.get("_aggregated_best_score")
                or row.get("@search.reranker_score")
                or row.get("@search.score")
                or row.get("score")
                or 0.0
            )
            candidates.append({
                "entity_id": row.get("entity_id", ""),
                "entity_name": row.get("entity_name", ""),
                "score": score,
                "_aggregated_best_score": score,
                "_support_count": row.get("_support_count", 1),
            })
        result["candidates_used"] = candidates

    result.setdefault("retrieval_views", [])
    return result


def normalize_historic_rag_for_ui(result: dict) -> dict:
    semantic = _normalize_candidate_rows(
        result.get("semantic_candidate_value_streams"),
        source="semantic",
    )
    historical = _normalize_candidate_rows(
        result.get("historical_candidate_value_streams") or result.get("historical_value_stream_support"),
        source="historical",
    )
    merged = _normalize_candidate_rows(
        result.get("merged_candidate_value_streams") or result.get("candidate_value_streams"),
        source="merged",
    )

    result["semantic_candidate_value_streams"] = semantic
    result["historical_candidate_value_streams"] = historical
    result["merged_candidate_value_streams"] = merged
    result["candidate_value_streams"] = merged
    result.setdefault("candidates_used", merged)
    result.setdefault("retrieval_views", [])
    return result
