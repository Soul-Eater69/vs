from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from clients.azure_direct_client import AzureDirectSearchClient
from sinks.faiss_store import faiss_index_exists, search_local_faiss
from text import clean_ppt_text, normalize_for_search
from pipelines.retrieval.pipeline import (
    build_historical_vs_evidence,
    load_ticket_vs_maps,
    retrieve_historical_chunks,
)

logger = logging.getLogger(__name__)


def retrieve_semantic_candidates(
    query: str,
    *,
    top_k: int = 12,
    allowed_value_stream_names: Optional[List[str]] = None,
) -> List[dict]:
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


def load_historical_summary_map(
    base_dir: str | Path | None = None,
    *,
    faiss_dir: str | Path | None = None,
) -> Dict[str, dict]:
    base = Path(base_dir or "ticket_data")
    mapping: Dict[str, dict] = {}

    faiss_summary_docs = Path(faiss_dir) / "summary_docs.json" if faiss_dir else None
    if faiss_summary_docs and faiss_summary_docs.exists():
        try:
            payload = json.loads(faiss_summary_docs.read_text(encoding="utf-8"))
            for record in payload or []:
                if not isinstance(record, dict):
                    continue
                ticket_id = str(record.get("ticket_id") or "").strip()
                if ticket_id:
                    mapping[ticket_id] = record
        except Exception as exc:
            logger.warning("Failed loading %s: %s", faiss_summary_docs, exc)

    aggregate_path = base / "_all_summaries.json"
    if aggregate_path.exists():
        try:
            payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
            records = payload.get("summaries", []) if isinstance(payload, dict) else payload
            for record in records or []:
                if not isinstance(record, dict):
                    continue
                ticket_id = str(record.get("ticket_id") or "").strip()
                if ticket_id:
                    mapping[ticket_id] = record
        except Exception as exc:
            logger.warning("Failed loading %s: %s", aggregate_path, exc)

    if base.exists():
        for path in sorted(base.glob("**/summary.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed loading %s: %s", path, exc)
                continue

            if not isinstance(record, dict):
                continue
            ticket_id = str(record.get("ticket_id") or "").strip()
            if ticket_id and ticket_id not in mapping:
                mapping[ticket_id] = record

    logger.info("[HIST-RAG] Loaded %d historical summary docs", len(mapping))
    return mapping


def retrieve_historical_support(
    query: str,
    *,
    historical_summary_dir: str | Path = "ticket_data",
    historical_faiss_dir: str | Path = "ticket_data/_faiss",
    local_vs_map_dir: str | Path = "ticket_chunks",
    historical_backend: str = "auto",
    top_per_view: int = 12,
    max_historical_chunks: int = 60,
    max_ticket_hits: int = 12,
) -> dict:
    cleaned = clean_ppt_text(query)
    summary_map = load_historical_summary_map(historical_summary_dir, faiss_dir=historical_faiss_dir)

    if historical_backend not in {"auto", "faiss", "chunks"}:
        raise ValueError("historical_backend must be one of: auto, faiss, chunks")

    if historical_backend in {"auto", "faiss"} and faiss_index_exists(
        index_dir=historical_faiss_dir,
        kind="summaries",
    ):
        faiss_hits = _search_summary_faiss(
            cleaned,
            historical_faiss_dir=historical_faiss_dir,
            top_k=max_ticket_hits,
        )
        vs_support = _build_support_from_summaries(faiss_hits, summary_map) if summary_map else []
        return {
            "historical_chunks": [],
            "historical_ticket_hits": faiss_hits,
            "historical_value_stream_support": vs_support,
            "historical_source": "summary_faiss",
        }

    if historical_backend == "faiss":
        logger.warning("FAISS backend requested but no local summary FAISS index was found at %s", historical_faiss_dir)

    historical_chunks = retrieve_historical_chunks(cleaned, top_k_per_view=top_per_view)
    historical_chunks = _limit_historical_chunks(historical_chunks, max_historical_chunks)

    if summary_map:
        ticket_hits = _rank_ticket_hits(historical_chunks, max_ticket_hits=max_ticket_hits)
        vs_support = _build_support_from_summaries(ticket_hits, summary_map)
        return {
            "historical_chunks": historical_chunks,
            "historical_ticket_hits": ticket_hits,
            "historical_value_stream_support": vs_support,
            "historical_source": "azure_chunks_plus_summary_docs",
        }

    ticket_vs_map = load_ticket_vs_maps(local_vs_map_dir)
    ticket_hits, legacy_support = build_historical_vs_evidence(
        historical_chunks,
        ticket_vs_map,
        max_ticket_hits=max_ticket_hits,
    )
    adapted = [_adapt_legacy_support_row(row) for row in legacy_support]
    return {
        "historical_chunks": historical_chunks,
        "historical_ticket_hits": ticket_hits,
        "historical_value_stream_support": adapted,
        "historical_source": "ticket_vs_maps",
    }


def _search_summary_faiss(
    query: str,
    *,
    historical_faiss_dir: str | Path,
    top_k: int,
) -> List[dict]:
    results = search_local_faiss(
        query,
        index_dir=historical_faiss_dir,
        kind="summaries",
        top_k=top_k,
    )
    out: List[dict] = []
    for row in results:
        metadata = row.get("metadata") or {}
        content = str(row.get("content") or "").strip()
        out.append(
            {
                "ticket_id": str(metadata.get("ticket_id") or "").strip(),
                "best_score": float(row.get("score", 0.0) or 0.0),
                "matched_chunk_ids": [],
                "title": str(metadata.get("ticket_id") or "").strip(),
                "summary_preview": content[:320],
            }
        )
    return [row for row in out if row.get("ticket_id")]


def _limit_historical_chunks(chunks: List[dict], limit: int) -> List[dict]:
    if limit <= 0:
        return []

    ranked = sorted(
        chunks,
        key=lambda chunk: float(chunk.get("_score", 0.0) or 0.0),
        reverse=True,
    )

    per_ticket_cap = 2
    per_ticket: Dict[str, int] = {}
    selected: List[dict] = []
    overflow: List[dict] = []

    for chunk in ranked:
        ticket_id = str(chunk.get("sourceId") or "").strip()
        if ticket_id:
            seen = per_ticket.get(ticket_id, 0)
            if seen >= per_ticket_cap:
                overflow.append(chunk)
                continue
            per_ticket[ticket_id] = seen + 1

        selected.append(chunk)
        if len(selected) >= limit:
            return selected

    for chunk in overflow:
        selected.append(chunk)
        if len(selected) >= limit:
            break

    return selected


def _rank_ticket_hits(chunks: List[dict], *, max_ticket_hits: int) -> List[dict]:
    by_ticket: Dict[str, dict] = {}
    for chunk in chunks:
        ticket_id = str(chunk.get("sourceId") or chunk.get("ticket_id") or "").strip()
        if not ticket_id:
            continue

        score = float(chunk.get("_score", 0.0) or 0.0)
        entry = by_ticket.setdefault(
            ticket_id,
            {
                "ticket_id": ticket_id,
                "best_score": score,
                "matched_chunk_ids": [],
                "title": str(chunk.get("title") or ""),
            },
        )
        entry["best_score"] = max(float(entry.get("best_score", 0.0)), score)

        chunk_id = str(chunk.get("id") or "").strip()
        if chunk_id and chunk_id not in entry["matched_chunk_ids"]:
            entry["matched_chunk_ids"].append(chunk_id)

    ranked = sorted(
        by_ticket.values(),
        key=lambda row: float(row.get("best_score", 0.0)),
        reverse=True,
    )
    return ranked[:max_ticket_hits]


def _build_support_from_summaries(ticket_hits: List[dict], summary_map: Dict[str, dict]) -> List[dict]:
    support_by_name: Dict[str, dict] = {}

    for hit in ticket_hits:
        ticket_id = str(hit.get("ticket_id") or "").strip()
        summary = summary_map.get(ticket_id)
        if not summary:
            continue

        score = float(hit.get("best_score", 0.0) or 0.0)
        for vs in _coerce_value_stream_rows(summary):
            name = str(vs.get("vs_name") or "").strip()
            if not name:
                continue

            inference_type = str(vs.get("inference_type") or "direct").strip().lower()
            reason = str(vs.get("reason") or "").strip()
            label_source = str(summary.get("label_source") or "unknown")

            entry = support_by_name.setdefault(
                name,
                {
                    "entity_id": str(vs.get("vs_id") or "").strip(),
                    "entity_name": name,
                    "support_count": 0,
                    "direct_count": 0,
                    "implied_count": 0,
                    "best_support_score": 0.0,
                    "avg_support_score": 0.0,
                    "supporting_ticket_ids": [],
                    "supporting_chunk_ids": [],
                    "historical_reasons": [],
                    "label_sources": [],
                },
            )

            entry["support_count"] += 1
            entry["best_support_score"] = max(float(entry["best_support_score"]), score)
            entry["avg_support_score"] += score

            if inference_type == "implied":
                entry["implied_count"] += 1
            else:
                entry["direct_count"] += 1

            if ticket_id and ticket_id not in entry["supporting_ticket_ids"]:
                entry["supporting_ticket_ids"].append(ticket_id)
            for chunk_id in hit.get("matched_chunk_ids", [])[:5]:
                if chunk_id and chunk_id not in entry["supporting_chunk_ids"]:
                    entry["supporting_chunk_ids"].append(chunk_id)
            if reason and reason not in entry["historical_reasons"]:
                entry["historical_reasons"].append(reason)
            if label_source and label_source not in entry["label_sources"]:
                entry["label_sources"].append(label_source)

    rows: List[dict] = []
    for row in support_by_name.values():
        support_count = max(int(row.get("support_count", 0)), 1)
        row["avg_support_score"] = round(float(row.get("avg_support_score", 0.0)) / support_count, 4)
        row["best_support_score"] = round(float(row.get("best_support_score", 0.0)), 4)
        row["historical_reasons"] = row["historical_reasons"][:3]
        rows.append(row)

    return sorted(
        rows,
        key=lambda item: (
            int(item.get("support_count", 0)),
            int(item.get("direct_count", 0)),
            float(item.get("best_support_score", 0.0)),
        ),
        reverse=True,
    )


def _coerce_value_stream_rows(summary: dict) -> List[dict]:
    rows = summary.get("value_streams") or []
    if isinstance(rows, list) and rows:
        out: List[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append(
                {
                    "vs_id": str(row.get("vs_id") or "").strip(),
                    "vs_name": str(row.get("vs_name") or "").strip(),
                    "inference_type": str(row.get("inference_type") or "direct").strip().lower(),
                    "reason": str(row.get("reason") or "").strip(),
                }
            )
        if out:
            return out

    direct_names = {
        str(name).strip()
        for name in (summary.get("direct_vs_names") or [])
        if str(name).strip()
    }
    implied_names = {
        str(name).strip()
        for name in (summary.get("implied_vs_names") or [])
        if str(name).strip()
    }
    value_stream_names = [str(name).strip() for name in (summary.get("value_stream_names") or []) if str(name).strip()]
    value_stream_ids = [str(vs_id).strip() for vs_id in (summary.get("value_stream_ids") or [])]

    rows = []
    for idx, name in enumerate(value_stream_names):
        inference_type = "implied" if name in implied_names and name not in direct_names else "direct"
        rows.append(
            {
                "vs_id": value_stream_ids[idx] if idx < len(value_stream_ids) else "",
                "vs_name": name,
                "inference_type": inference_type,
                "reason": "",
            }
        )
    return rows


def _adapt_legacy_support_row(row: dict) -> dict:
    support_count = int(row.get("support_count", 0) or 0)
    return {
        "entity_id": str(row.get("entity_id") or "").strip(),
        "entity_name": str(row.get("entity_name") or "").strip(),
        "support_count": support_count,
        "direct_count": support_count,
        "implied_count": 0,
        "best_support_score": round(float(row.get("best_support_score", 0.0) or 0.0), 4),
        "avg_support_score": round(float(row.get("best_support_score", 0.0) or 0.0), 4),
        "supporting_ticket_ids": list(row.get("supporting_ticket_ids") or []),
        "supporting_chunk_ids": list(row.get("supporting_chunk_ids") or []),
        "historical_reasons": [],
        "label_sources": [],
    }
