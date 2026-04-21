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

    Each FAISS hit already has resolved VS metadata. We prefer explicitly
    classified direct/implied buckets and only fall back to the full attached
    label list when no finer-grained classification exists.

    historical_reasons is populated per VS from each supporting ticket's
    summary_preview and the relevant canonical functions (implied or direct),
    so downstream prompts have analog card context rather than just counts.
    """
    support_by_name: Dict[str, dict] = {}

    for hit in ticket_hits:
        ticket_id = hit.get("ticket_id", "")
        score = float(hit.get("best_score", 0.0))
        summary_preview = str(hit.get("summary_preview") or "").strip()[:200]
        direct_funcs: List[str] = [f for f in (hit.get("direct_functions_canonical") or []) if f]
        implied_funcs: List[str] = [f for f in (hit.get("implied_functions_canonical") or []) if f]

        for item in _extract_hit_value_stream_support(hit):
            vs_name = item["entity_name"]
            vs_id = item["entity_id"]
            inference_type = item["inference_type"]
            per_ticket_weight = float(item["per_ticket_weight"])
            label_source = item["label_source"]

            entry = support_by_name.setdefault(
                vs_name,
                {
                    "entity_id": vs_id,
                    "entity_name": vs_name,
                    "support_count": 0,
                    "direct_count": 0,
                    "implied_count": 0,
                    "weighted_support_count": 0.0,
                    "weighted_direct_count": 0.0,
                    "weighted_implied_count": 0.0,
                    "best_support_score": 0.0,
                    "total_score": 0.0,
                    "supporting_ticket_ids": [],
                    "label_sources": [],
                    "historical_reasons": [],
                },
            )

            entry["support_count"] += 1
            entry["weighted_support_count"] += per_ticket_weight
            entry["best_support_score"] = max(entry["best_support_score"], score)
            entry["total_score"] += score

            if inference_type == "implied":
                entry["implied_count"] += 1
                entry["weighted_implied_count"] += per_ticket_weight
            else:
                entry["direct_count"] += 1
                entry["weighted_direct_count"] += per_ticket_weight

            if ticket_id and ticket_id not in entry["supporting_ticket_ids"]:
                entry["supporting_ticket_ids"].append(ticket_id)
            if label_source and label_source not in entry["label_sources"]:
                entry["label_sources"].append(label_source)

            # Build analog reason string: summary + functions that drove the mapping.
            # Keep up to 3 per VS so the prompt stays concise.
            if len(entry["historical_reasons"]) < 3:
                funcs = implied_funcs if inference_type == "implied" else direct_funcs
                parts: List[str] = []
                if summary_preview:
                    parts.append(summary_preview)
                if funcs:
                    parts.append(f"functions: {', '.join(funcs[:3])}")
                if parts:
                    prefix = f"[{ticket_id} / {inference_type}]"
                    entry["historical_reasons"].append(f"{prefix} {' | '.join(parts)}")

    rows: List[dict] = []
    for row in support_by_name.values():
        count = max(row["support_count"], 1)
        row["avg_support_score"] = round(row.pop("total_score") / count, 4)
        row["best_support_score"] = round(row["best_support_score"], 4)
        row["weighted_support_count"] = round(float(row["weighted_support_count"]), 4)
        row["weighted_direct_count"] = round(float(row["weighted_direct_count"]), 4)
        row["weighted_implied_count"] = round(float(row["weighted_implied_count"]), 4)
        rows.append(row)

    return sorted(
        rows,
        key=lambda r: (
            float(r.get("weighted_support_count", 0.0)),
            float(r.get("weighted_direct_count", 0.0)),
            r["best_support_score"],
        ),
        reverse=True,
    )


def _extract_hit_value_stream_support(hit: dict) -> List[dict]:
    label_source = str(hit.get("label_source") or "").strip()
    fallback_names = _dedupe_text_list(
        hit.get("value_stream_names")
        or hit.get("value_stream_labels")
        or []
    )
    fallback_ids = [str(value).strip() for value in (hit.get("value_stream_ids") or [])]
    id_by_name = {
        name: fallback_ids[idx]
        for idx, name in enumerate(fallback_names)
        if idx < len(fallback_ids) and fallback_ids[idx]
    }

    direct_names = _dedupe_text_list(hit.get("direct_vs_names") or [])
    direct_set = {name.lower() for name in direct_names}
    implied_names = [
        name
        for name in _dedupe_text_list(hit.get("implied_vs_names") or [])
        if name.lower() not in direct_set
    ]

    support_types = hit.get("stream_support_type") or {}
    if not direct_names and not implied_names and isinstance(support_types, dict):
        for name in fallback_names:
            inference_type = str(support_types.get(name, "")).strip().lower()
            if inference_type == "direct":
                direct_names.append(name)
                direct_set.add(name.lower())
            elif inference_type == "implied" and name.lower() not in direct_set:
                implied_names.append(name)

    if not direct_names and not implied_names:
        fallback_inference = "direct" if label_source == "jira_issuelinks" else "implied"
        if fallback_inference == "direct":
            direct_names = list(fallback_names)
            direct_set = {name.lower() for name in direct_names}
        else:
            implied_names = list(fallback_names)

    stream_names = direct_names + [name for name in implied_names if name.lower() not in direct_set]
    if not stream_names:
        return []

    per_ticket_weight = 1.0 / max(len(stream_names), 1)
    rows: List[dict] = []
    for name in direct_names:
        rows.append(
            {
                "entity_name": name,
                "entity_id": id_by_name.get(name, ""),
                "inference_type": "direct",
                "label_source": label_source,
                "per_ticket_weight": per_ticket_weight,
            }
        )
    for name in implied_names:
        rows.append(
            {
                "entity_name": name,
                "entity_id": id_by_name.get(name, ""),
                "inference_type": "implied",
                "label_source": label_source,
                "per_ticket_weight": per_ticket_weight,
            }
        )
    return rows


def _dedupe_text_list(values: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        clean = str(value).strip()
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out
