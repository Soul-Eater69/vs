from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional

from ..query.views import clean_ppt_text
logger = logging.getLogger(__name__)


def retrieve_historical_support(
    query: str,
    *,
    historical_faiss_dir: str | Path = "ticket_data/_faiss",
    max_ticket_hits: int = 12,
    exclude_ticket_ids: Optional[Iterable[str]] = None,
) -> dict:
    from vs_app.integrations.sinks.faiss_store import faiss_index_exists, search_local_faiss

    cleaned = clean_ppt_text(query)

    excluded = {
        _normalize_ticket_id(tid)
        for tid in (exclude_ticket_ids or [])
        if _normalize_ticket_id(tid)
    }

    if not faiss_index_exists(index_dir=historical_faiss_dir, kind="summaries"):
        logger.warning("No FAISS index at %s - no historical support available", historical_faiss_dir)
        return {
            "historical_ticket_hits": [],
            "historical_value_stream_support": [],
            "historical_source": "none",
        }

    exclusion_backfill = 0
    if excluded:
        exclusion_backfill = max(8, len(excluded) * 3)
    fetch_k = max_ticket_hits + exclusion_backfill
    faiss_results = search_local_faiss(
        cleaned,
        index_dir=historical_faiss_dir,
        kind="summaries",
        top_k=fetch_k,
    )

    ticket_hits: List[dict] = []
    for row in faiss_results:
        meta = row.get("metadata") or {}
        ticket_id = str(meta.get("ticket_id") or "").strip()
        if not ticket_id:
            continue
        if _normalize_ticket_id(ticket_id) in excluded:
            continue
        if len(ticket_hits) >= max_ticket_hits:
            break
        ticket_hits.append({
            "ticket_id": ticket_id,
            "best_score": float(row.get("score", 0.0) or 0.0),
            "title": str(meta.get("title") or ticket_id),
            "summary_preview": str(row.get("content") or "")[:320],
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

    filtered_hits = filter_ticket_hits(ticket_hits, exclude_ticket_ids)
    vs_support = _build_support_from_faiss_hits(filtered_hits)

    return {
        "historical_ticket_hits": filtered_hits,
        "historical_value_stream_support": vs_support,
        "historical_source": "summary_faiss",
    }


def filter_historical_result(result: dict, exclude_ticket_ids: Optional[Iterable[str]]) -> dict:
    """Remove excluded source tickets from historical hits and derived support."""
    filtered_hits = filter_ticket_hits(result.get("historical_ticket_hits", []), exclude_ticket_ids)
    if len(filtered_hits) == len(result.get("historical_ticket_hits", []) or []):
        return result

    payload = dict(result)
    payload["historical_ticket_hits"] = filtered_hits
    payload["historical_value_stream_support"] = _build_support_from_faiss_hits(filtered_hits)
    return payload


def filter_ticket_hits(ticket_hits: List[dict], exclude_ticket_ids: Optional[Iterable[str]]) -> List[dict]:
    excluded = {
        _normalize_ticket_id(tid)
        for tid in (exclude_ticket_ids or [])
        if _normalize_ticket_id(tid)
    }
    if not excluded:
        return list(ticket_hits or [])
    return [
        hit
        for hit in (ticket_hits or [])
        if _normalize_ticket_id(hit.get("ticket_id")) not in excluded
    ]


def _build_support_from_faiss_hits(ticket_hits: List[dict]) -> List[dict]:
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
        key=lambda row: (
            float(row.get("weighted_support_count", 0.0)),
            float(row.get("weighted_direct_count", 0.0)),
            row["best_support_score"],
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


def _normalize_ticket_id(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    match = re.search(r"\b[A-Z][A-Z0-9_]*-\d+\b", text)
    return match.group(0) if match else text
