from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...clients.azure_direct_client import AzureDirectSearchClient
from ...sinks.faiss_store import faiss_index_exists, search_local_faiss
from ...text import clean_ppt_text, normalize_for_search

logger = logging.getLogger(__name__)


def retrieve_semantic_candidates(
    query: str,
    *,
    top_k: int = 12,
    allowed_value_stream_names: Optional[List[str]] = None,
) -> List[dict]:
    """Retrieve value stream candidates from Azure AI Search."""
    client = AzureDirectSearchClient()
    allowed = {
        normalize_for_search(str(name))
        for name in (allowed_value_stream_names or [])
        if str(name).strip()
    }

    try:
        results = client.search_hybrid(
            query,
            top_k=top_k,
            use_semantic_rerank=True,
            filter_expression="node_type eq 'ValueStream'",
        )
    except Exception as exc:
        logger.warning("Hybrid semantic retrieval unavailable, falling back to vector search: %s", exc)
        results = client.search_vector(
            query,
            top_k=top_k,
            filter_expression="node_type eq 'ValueStream'",
        )

    dedup: Dict[str, dict] = {}
    for row in results or []:
        name = str(row.get("entity_name") or "").strip()
        entity_id = str(row.get("entity_id") or "").strip()
        if not name:
            continue

        name_key = normalize_for_search(name)
        if allowed and name_key not in allowed:
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


def retrieve_historical_support(
    query: str,
    *,
    historical_faiss_dir: str | Path = "ticket_data/_faiss",
    max_ticket_hits: int = 12,
) -> dict:
    """
    Retrieve historical ticket analogs from FAISS and extract their VS labels.

    FAISS documents already contain value_stream_labels in metadata —
    no need to load ticket_chunks/ or summary_map files.
    """
    cleaned = clean_ppt_text(query)

    if not faiss_index_exists(index_dir=historical_faiss_dir, kind="summaries"):
        logger.warning("No FAISS index at %s — no historical support available", historical_faiss_dir)
        return {
            "historical_ticket_hits": [],
            "historical_value_stream_support": [],
            "historical_source": "none",
        }

    # Search FAISS — get back ticket summaries with VS labels already in metadata
    faiss_results = search_local_faiss(
        cleaned,
        index_dir=historical_faiss_dir,
        kind="summaries",
        top_k=max_ticket_hits,
    )

    ticket_hits: List[dict] = []
    for row in faiss_results:
        meta = row.get("metadata") or {}
        ticket_id = str(meta.get("ticket_id") or "").strip()
        if not ticket_id:
            continue
        ticket_hits.append({
            "ticket_id": ticket_id,
            "best_score": float(row.get("score", 0.0) or 0.0),
            "title": str(meta.get("title") or ticket_id),
            "summary_preview": str(row.get("content") or "")[:320],
            # Support both the newer rag-summary shape and the legacy local FAISS shape.
            "value_stream_labels": meta.get("value_stream_labels", []),
            "value_stream_names": meta.get("value_stream_names", []),
            "value_stream_ids": meta.get("value_stream_ids", []),
            "stream_support_type": meta.get("stream_support_type", {}),
            "direct_vs_names": meta.get("direct_vs_names", []),
            "implied_vs_names": meta.get("implied_vs_names", []),
            "label_source": meta.get("label_source", ""),
            "direct_functions_canonical": meta.get("direct_functions_canonical", []),
            "implied_functions_canonical": meta.get("implied_functions_canonical", []),
        })

    # Build VS support directly from FAISS metadata — no file loading needed
    vs_support = _build_support_from_faiss_hits(ticket_hits)

    return {
        "historical_ticket_hits": ticket_hits,
        "historical_value_stream_support": vs_support,
        "historical_source": "summary_faiss",
    }


def _build_support_from_faiss_hits(ticket_hits: List[dict]) -> List[dict]:
    """
    Aggregate VS support counts from FAISS results.

    Each FAISS hit already has value_stream_labels and stream_support_type
    in its metadata, so we just aggregate.
    """
    support_by_name: Dict[str, dict] = {}

    for hit in ticket_hits:
        ticket_id = hit.get("ticket_id", "")
        score = float(hit.get("best_score", 0.0))
        vs_labels = _resolve_hit_value_stream_names(hit)
        vs_ids = hit.get("value_stream_ids") or []
        support_types = hit.get("stream_support_type") or {}
        direct_vs_names = {
            str(value).strip()
            for value in (hit.get("direct_vs_names") or [])
            if str(value).strip()
        }
        implied_vs_names = {
            str(value).strip()
            for value in (hit.get("implied_vs_names") or [])
            if str(value).strip()
        }

        for idx, vs_name in enumerate(vs_labels):
            vs_name = str(vs_name).strip()
            if not vs_name:
                continue

            vs_id = str(vs_ids[idx]).strip() if idx < len(vs_ids) else ""
            inference_type = str(support_types.get(vs_name, "")).lower()
            if not inference_type:
                if vs_name in implied_vs_names and vs_name not in direct_vs_names:
                    inference_type = "implied"
                else:
                    inference_type = "direct"

            entry = support_by_name.setdefault(
                vs_name,
                {
                    "entity_id": vs_id,
                    "entity_name": vs_name,
                    "support_count": 0,
                    "direct_count": 0,
                    "implied_count": 0,
                    "best_support_score": 0.0,
                    "total_score": 0.0,
                    "supporting_ticket_ids": [],
                },
            )

            entry["support_count"] += 1
            entry["best_support_score"] = max(entry["best_support_score"], score)
            entry["total_score"] += score

            if inference_type == "implied":
                entry["implied_count"] += 1
            else:
                entry["direct_count"] += 1

            if ticket_id and ticket_id not in entry["supporting_ticket_ids"]:
                entry["supporting_ticket_ids"].append(ticket_id)

    rows: List[dict] = []
    for row in support_by_name.values():
        count = max(row["support_count"], 1)
        row["avg_support_score"] = round(row.pop("total_score") / count, 4)
        row["best_support_score"] = round(row["best_support_score"], 4)
        rows.append(row)

    return sorted(
        rows,
        key=lambda r: (r["support_count"], r["direct_count"], r["best_support_score"]),
        reverse=True,
    )


def _resolve_hit_value_stream_names(hit: dict) -> List[str]:
    labels = [
        str(value).strip()
        for value in (hit.get("value_stream_labels") or [])
        if str(value).strip()
    ]
    if labels:
        return labels

    names = [
        str(value).strip()
        for value in (hit.get("value_stream_names") or [])
        if str(value).strip()
    ]
    if names:
        return names

    combined: List[str] = []
    for field in ("direct_vs_names", "implied_vs_names"):
        for value in hit.get(field) or []:
            clean = str(value).strip()
            if clean and clean not in combined:
                combined.append(clean)
    return combined
