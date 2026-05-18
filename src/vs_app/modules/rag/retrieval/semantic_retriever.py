from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from ..query.views import normalize_for_search
logger = logging.getLogger(__name__)

_SEMANTIC_QUERY_MAX_CHARS = 900
_SEMANTIC_QUERY_MAX_TERMS = 90
_SEMANTIC_SEARCH_FIELDS = ["entity_name", "content"]


def retrieve_semantic_candidates(
    query: str,
    *,
    top_k: int = 12,
    client: Any = None,
    allowed_value_stream_names: Optional[Iterable[str]] = None,
) -> List[dict]:
    allowed_names = {
        normalize_for_search(str(name))
        for name in (allowed_value_stream_names or [])
        if str(name or "").strip()
    }
    search_top_k = top_k
    if allowed_names:
        search_top_k = min(100, max(top_k, top_k * 2))

    if client is None:
        from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient

        client = AzureDirectSearchClient()

    try:
        search_query = _semantic_search_query(query)
        results = client.search_hybrid(
            search_query,
            top_k=search_top_k,
            use_semantic_rerank=True,
            filter_expression="node_type eq 'ValueStream'",
            search_fields=_SEMANTIC_SEARCH_FIELDS,
        )
    except Exception as exc:
        logger.warning("Hybrid semantic retrieval unavailable, falling back to vector search: %s", exc)
        results = client.search_vector(
            _semantic_search_query(query),
            top_k=search_top_k,
            filter_expression="node_type eq 'ValueStream'",
        )

    dedup: Dict[str, dict] = {}
    for row in results or []:
        name = str(row.get("entity_name") or "").strip()
        entity_id = str(row.get("entity_id") or "").strip()
        if not name:
            continue

        name_key = normalize_for_search(name)
        if allowed_names and name_key not in allowed_names:
            continue

        score = float(
            row.get("@search.reranker_score")
            if row.get("@search.reranker_score") is not None
            else row.get("@search.score", 0.0)
            or 0.0
        )
        key = entity_id or name_key
        existing = dedup.get(key)
        if existing and float(existing.get("semantic_score", 0.0)) >= score:
            continue

        description = str(row.get("content") or row.get("description") or "").strip()
        dedup[key] = {
            "entity_id": entity_id,
            "entity_name": name,
            "description": description,
            "semantic_score": round(score, 4),
            "from_semantic": True,
            "from_historical": False,
        }

    return sorted(
        dedup.values(),
        key=lambda item: float(item.get("semantic_score", 0.0)),
        reverse=True,
    )


def _semantic_search_query(query: str) -> str:
    normalized = normalize_for_search(query, max_chars=_SEMANTIC_QUERY_MAX_CHARS)
    if not normalized:
        return " "

    terms: list[str] = []
    seen: set[str] = set()
    for term in normalized.split():
        if len(terms) >= _SEMANTIC_QUERY_MAX_TERMS:
            break
        if len(term) <= 1:
            continue
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return " ".join(terms) or normalized[:_SEMANTIC_QUERY_MAX_CHARS] or " "
