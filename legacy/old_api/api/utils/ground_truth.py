from __future__ import annotations

import json
import logging

from core import compare_value_streams, get_value_streams_for_idea_card_sync
from core.constants import CANONICAL_VALUE_STREAMS

from api.config import TICKET_CHUNKS_DIR, HISTORICAL_FAISS_DIR
from api.models import GenerateRequest

logger = logging.getLogger(__name__)


def _coerce_vs_names(payload: dict) -> list[str]:
    names = [
        str(n).strip()
        for n in (payload.get("value_stream_names") or payload.get("value_stream_labels") or [])
        if str(n).strip()
    ]
    if names:
        return names

    rows = payload.get("value_streams") or []
    if isinstance(rows, list):
        extracted = [
            str(row.get("vs_name") or row.get("name") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("vs_name") or row.get("name") or "").strip()
        ]
        if extracted:
            return extracted

    fallback: list[str] = []
    for key in ("direct_vs_names", "implied_vs_names"):
        fallback.extend(
            str(n).strip() for n in (payload.get(key) or []) if str(n).strip()
        )
    return list(dict.fromkeys(fallback))


def _load_faiss_summary_docs() -> list[dict]:
    docs_path = HISTORICAL_FAISS_DIR / "summary_docs.json"
    if not docs_path.exists():
        return []
    with open(docs_path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        return [r for r in (payload.get("summaries") or []) if isinstance(r, dict)]
    return []


def load_ticket_chunk_ground_truth(doc_id: str) -> dict:
    map_path = TICKET_CHUNKS_DIR / doc_id / "08_valuestream_map.json"
    if not map_path.exists():
        return {"value_streams": [], "title": doc_id, "found": False}
    try:
        with open(map_path, encoding="utf-8") as f:
            payload = json.load(f)
        return {"value_streams": payload.get("valueStreamNames", []) or [], "title": doc_id, "found": True}
    except Exception as exc:
        logger.error("Error loading ticket chunk GT: %s", exc, exc_info=True)
        return {"value_streams": [], "title": doc_id, "found": False}


def load_faiss_ground_truth(doc_id: str) -> dict:
    key = str(doc_id or "").strip().lower()
    if not key:
        return {"value_streams": [], "title": doc_id, "found": False}
    try:
        for row in _load_faiss_summary_docs():
            ticket_id = str(row.get("ticket_id") or row.get("doc_id") or "").strip()
            if ticket_id.lower() != key:
                continue
            return {
                "value_streams": _coerce_vs_names(row),
                "title": str(row.get("title") or ticket_id or doc_id).strip(),
                "found": True,
            }
    except Exception as exc:
        logger.error("Error loading FAISS ground truth for %s: %s", doc_id, exc, exc_info=True)
    return {"value_streams": [], "title": doc_id, "found": False}


def enrich_and_attach_ground_truth(
    result: dict,
    req: GenerateRequest,
    ground_truth_source: str = "jira",
) -> dict:
    result["source_doc_id"] = req.doc_id
    result["source_type"] = "ppt" if req.doc_id else "text"

    canonical_by_name = {vs.get("name", "").lower(): vs for vs in CANONICAL_VALUE_STREAMS}
    for sel in result.get("selected_value_streams", []):
        canonical = canonical_by_name.get(sel.get("entity_name", "").lower())
        if canonical:
            sel["category"] = canonical.get("category")
            sel["canonical_id"] = canonical.get("id")
            if not sel.get("entity_id"):
                sel["entity_id"] = canonical.get("id")

    if req.doc_id:
        if ground_truth_source == "historical_faiss":
            gt_data = load_faiss_ground_truth(req.doc_id)
            if not gt_data["found"]:
                gt_data = load_ticket_chunk_ground_truth(req.doc_id)
        elif ground_truth_source == "ticket_chunks":
            gt_data = load_ticket_chunk_ground_truth(req.doc_id)
        else:
            gt_data = get_value_streams_for_idea_card_sync(req.doc_id)

        if gt_data["found"]:
            result["ground_truth"] = gt_data["value_streams"]
            result["ground_truth_title"] = gt_data["title"]

            predicted = [vs.get("entity_name", "") for vs in result.get("selected_value_streams", [])]
            comparison = compare_value_streams(
                predicted=predicted,
                ground_truth=result["ground_truth"],
                fuzzy_threshold=0.75,
            )
            result["comparison"] = comparison

            match_map = {m.get("predicted", ""): m.get("ground_truth", "") for m in comparison.get("matches", [])}
            for sel in result.get("selected_value_streams", []):
                matched = match_map.get(sel.get("entity_name", ""))
                sel["is_match"] = bool(matched)
                if matched:
                    sel["matched_to_ground_truth"] = matched
        else:
            result["ground_truth"] = []

    result["canonical_value_streams"] = CANONICAL_VALUE_STREAMS
    return result
