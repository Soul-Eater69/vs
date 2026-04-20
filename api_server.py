"""
FastAPI backend for the Value Stream Explorer UI.

Run with:
    uvicorn api_server:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import traceback
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rag_summary.config.logging_config import configure_graph_logging

try:
    from vs.pipelines.historical_rag import run_historical_rag_pipeline
except ImportError:
    from pipelines.historical_rag import run_historical_rag_pipeline

logger = logging.getLogger(__name__)
configure_graph_logging(level=logging.INFO)

# ---------------------------------------------------------------------------
# Import from new core / pipelines packages
# ---------------------------------------------------------------------------
from core import clean_ppt_text, compare_value_streams, get_value_streams_for_idea_card_sync
from core.constants import CANONICAL_VALUE_STREAMS, MAPPINGS_PATH, IDEA_CARDS_DIR, ROOT_DIR
from src.pipelines import select_value_streams, generate_value_streams_rag, run_vector_search
from src.services.value_stream.value_stream_service import ValueStreamService
from rag_summary.pipeline import run_summary_rag_pipeline
from rag_summary.ingestion.faiss_indexer import DEFAULT_INDEX_DIR

# ---------------------------------------------------------------------------
# Canonical data alias (used throughout this module)
# ---------------------------------------------------------------------------
_CANONICAL_VALUE_STREAMS = CANONICAL_VALUE_STREAMS
_MAPPINGS_PATH = MAPPINGS_PATH
_IDEA_CARDS_DIR = IDEA_CARDS_DIR
_TICKET_CHUNKS_DIR = pathlib.Path(
    os.environ.get("TICKET_CHUNKS_DIR", str(ROOT_DIR / "ticket_chunks"))
).resolve()
_SUMMARY_INDEX_DIR = pathlib.Path(
    os.environ.get("SUMMARY_INDEX_DIR", DEFAULT_INDEX_DIR)
).resolve()
_HISTORICAL_FAISS_DIR = pathlib.Path(
    os.environ.get("HISTORICAL_FAISS_DIR", "ticket_data/_faiss")
).resolve()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Value Stream Explorer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    doc_id: Optional[str] = None
    query_text: Optional[str] = None
    fetch_count: int = 12
    approach: str = "plain"  # "plain" | "rag" | "summary-rag" | "historic-rag"
    allowed_value_stream_names: Optional[list[str]] = None

class SearchRequest(BaseModel):
    query: str
    top_k: int = 15

class ComparisonRequest(BaseModel):
    predicted: list[str]
    ground_truth: list[str]
    fuzzy_threshold: float = 0.75


def _normalize_approach(value: Optional[str]) -> str:
    raw = (value or "plain").strip().lower()
    if not raw:
        return "plain"

    normalized = raw.lstrip("/")
    if normalized.startswith("api/"):
        normalized = normalized[4:]

    aliases = {
        "generate": "plain",
        "plain": "plain",
        "rag": "rag",
        "generate-rag": "rag",
        "summary-rag": "summary-rag",
        "generate-trace": "summary-rag",
        "summary": "summary-rag",
        "historic-rag": "historic-rag",
        "historic": "historic-rag",
    }
    return aliases.get(normalized, normalized)


def _is_gateway_timeout_error(exc: Exception) -> bool:
    stack = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)

        status_code = getattr(current, "status_code", None)
        if status_code == 504:
            return True

        response = getattr(current, "response", None)
        if response is not None and getattr(response, "status_code", None) == 504:
            return True

        message = str(current).lower()
        if "gateway timeout" in message:
            return True
        if "504" in message and "timeout" in message:
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, Exception):
            stack.append(cause)
        if isinstance(context, Exception):
            stack.append(context)
    return False


def _normalize_for_ui(result: dict) -> dict:
    """Normalize plain/rag outputs to a common shape expected by the UI."""
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

    if "retrieval_views" not in result:
        result["retrieval_views"] = []

    return result


def _score_candidate_for_ui(row: dict) -> float:
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


def _normalize_candidate_rows_for_ui(rows: list[dict] | None, source: str) -> list[dict]:
    normalized: list[dict] = []
    for raw_row in rows or []:
        row = dict(raw_row or {})
        score = _score_candidate_for_ui(row)
        row.setdefault("score", score)
        row.setdefault("_aggregated_best_score", score)

        support_count = int(row.get("support_count") or row.get("_support_count") or 0)
        if support_count:
            row.setdefault("_support_count", support_count)

        row.setdefault("candidate_source", source)
        normalized.append(row)
    return normalized


def _normalize_historic_rag_for_ui(result: dict) -> dict:
    semantic_candidates = _normalize_candidate_rows_for_ui(
        result.get("semantic_candidate_value_streams"),
        source="semantic",
    )
    historical_candidates = _normalize_candidate_rows_for_ui(
        result.get("historical_candidate_value_streams") or result.get("historical_value_stream_support"),
        source="historical",
    )
    merged_candidates = _normalize_candidate_rows_for_ui(
        result.get("merged_candidate_value_streams") or result.get("candidate_value_streams"),
        source="merged",
    )

    result["semantic_candidate_value_streams"] = semantic_candidates
    result["historical_candidate_value_streams"] = historical_candidates
    result["merged_candidate_value_streams"] = merged_candidates
    result["candidate_value_streams"] = merged_candidates

    if "candidates_used" not in result:
        result["candidates_used"] = merged_candidates
    if "retrieval_views" not in result:
        result["retrieval_views"] = []

    return result


def _load_ticket_chunk_ground_truth(doc_id: str) -> dict:
    map_path = _TICKET_CHUNKS_DIR / doc_id / "08_valuestream_map.json"
    if not map_path.exists():
        return {"value_streams": [], "title": doc_id, "found": False}

    try:
        with open(map_path, encoding="utf-8") as f:
            payload = json.load(f)
        vs_list = payload.get("valueStreamNames", []) or []
        return {
            "value_streams": vs_list,
            "title": doc_id,
            "found": True,
        }
    except Exception as exc:
        logger.error(f"Error loading ticket chunk GT: {exc}", exc_info=True)
        return {"value_streams": [], "title": doc_id, "found": False}


def _coerce_ground_truth_value_streams(payload: dict) -> list[str]:
    names = [
        str(name).strip()
        for name in (payload.get("value_stream_names") or payload.get("value_stream_labels") or [])
        if str(name).strip()
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

    fallback = []
    for key in ("direct_vs_names", "implied_vs_names"):
        fallback.extend(
            str(name).strip()
            for name in (payload.get(key) or [])
            if str(name).strip()
        )
    return list(dict.fromkeys(fallback))


def _load_historical_faiss_summary_docs() -> list[dict]:
    docs_path = _HISTORICAL_FAISS_DIR / "summary_docs.json"
    if not docs_path.exists():
        return []

    with open(docs_path, encoding="utf-8") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [row for row in (payload.get("summaries") or []) if isinstance(row, dict)]
    return []


def _load_historical_faiss_ground_truth(doc_id: str) -> dict:
    doc_id_key = str(doc_id or "").strip().lower()
    if not doc_id_key:
        return {"value_streams": [], "title": doc_id, "found": False}

    try:
        for row in _load_historical_faiss_summary_docs():
            ticket_id = str(row.get("ticket_id") or row.get("doc_id") or "").strip()
            if ticket_id.lower() != doc_id_key:
                continue

            value_streams = _coerce_ground_truth_value_streams(row)
            return {
                "value_streams": value_streams,
                "title": str(row.get("title") or ticket_id or doc_id).strip(),
                "found": True,
            }
    except Exception as exc:
        logger.error("Error loading FAISS ground truth for %s: %s", doc_id, exc, exc_info=True)

    return {"value_streams": [], "title": doc_id, "found": False}


def _enrich_and_attach_ground_truth(
    result: dict,
    req: GenerateRequest,
    ground_truth_source: str = "jira",
) -> dict:
    # Attach source info
    result["source_doc_id"] = req.doc_id
    result["source_type"] = "ppt" if req.doc_id else "text"

    # Enrich selected value streams with canonical metadata
    canonical_by_name = {vs.get("name", "").lower(): vs for vs in _CANONICAL_VALUE_STREAMS}
    for selected_vs in result.get("selected_value_streams", []):
        name_lower = selected_vs.get("entity_name", "").lower()
        if name_lower in canonical_by_name:
            canonical = canonical_by_name[name_lower]
            selected_vs["category"] = canonical.get("category")
            selected_vs["canonical_id"] = canonical.get("id")
            if not selected_vs.get("entity_id"):
                selected_vs["entity_id"] = canonical.get("id")

    # Enrich with ground truth if doc_id is known
    if req.doc_id:
        if ground_truth_source == "historical_faiss":
            ground_truth_data = _load_historical_faiss_ground_truth(req.doc_id)
            if not ground_truth_data["found"]:
                ground_truth_data = _load_ticket_chunk_ground_truth(req.doc_id)
        elif ground_truth_source == "ticket_chunks":
            ground_truth_data = _load_ticket_chunk_ground_truth(req.doc_id)
        else:
            ground_truth_data = get_value_streams_for_idea_card_sync(req.doc_id)

        if ground_truth_data["found"]:
            result["ground_truth"] = ground_truth_data["value_streams"]
            result["ground_truth_title"] = ground_truth_data["title"]

            predicted_names = [vs.get("entity_name", "") for vs in result.get("selected_value_streams", [])]
            comparison = compare_value_streams(
                predicted=predicted_names,
                ground_truth=result["ground_truth"],
                fuzzy_threshold=0.75,
            )
            result["comparison"] = comparison

            match_map = {
                m.get("predicted", ""): m.get("ground_truth", "")
                for m in comparison.get("matches", [])
            }
            for selected_vs in result.get("selected_value_streams", []):
                predicted_name = selected_vs.get("entity_name", "")
                matched_gt = match_map.get(predicted_name)
                selected_vs["is_match"] = bool(matched_gt)
                if matched_gt:
                    selected_vs["matched_to_ground_truth"] = matched_gt
        else:
            result["ground_truth"] = []

    # Include canonical value streams reference
    result["canonical_value_streams"] = _CANONICAL_VALUE_STREAMS
    return result

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/canonical-value-streams")
def get_canonical_value_streams() -> dict:
    """Return all canonical/default value streams by category."""
    by_category = {}
    for vs in _CANONICAL_VALUE_STREAMS:
        cat = vs.get("category", "uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append({
            "id": vs.get("id"),
            "name": vs.get("name"),
            "category": cat,
        })
    return {
        "total": len(_CANONICAL_VALUE_STREAMS),
        "by_category": by_category,
        "all": _CANONICAL_VALUE_STREAMS,
    }


@app.get("/api/idea-cards")
def list_idea_cards() -> dict:
    """Return available idea card files from the static/idea_cards directory (PDF, PPTX, DOCX, etc.)."""
    cards = []
    seen = set()

    # Load all files from static/idea_cards directory
    if _IDEA_CARDS_DIR.exists():
        for file_path in sorted(_IDEA_CARDS_DIR.iterdir()):
            if file_path.is_file():
                doc_id = file_path.stem  # filename without extension
                extension = file_path.suffix[1:] if file_path.suffix else "unknown"  # extension without dot

                if doc_id:
                    seen.add(doc_id)

                    # Check if there's a mapping entry with a better title
                    display_name = doc_id
                    has_mapping = False
                    if _MAPPINGS_PATH.exists():
                        with open(_MAPPINGS_PATH, encoding="utf-8") as f:
                            mappings = json.load(f)
                            for entry in mappings.get("idea_cards", []):
                                if entry.get("doc_id") == doc_id:
                                    has_mapping = True
                                    display_name = entry.get("title", doc_id)
                                    break

                    cards.append({
                        "doc_id": doc_id,
                        "display_name": display_name,
                        "file_name": file_path.name,
                        "extension": extension,
                        "has_mapping": has_mapping,
                    })

    return {"cards": cards}


@app.get("/api/mappings")
def get_mappings() -> dict:
    """Return the ground-truth mappings from mappings.json."""
    if not _MAPPINGS_PATH.exists():
        raise HTTPException(status_code=404, detail="mappings.json not found")
    with open(_MAPPINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/summary-index/docs")
def get_summary_index_docs(limit: int = 50, offset: int = 0) -> dict:
    """
    Return persisted summary docs from the local FAISS summary index for quick inspection.

    Query params:
      - limit: max docs to return (default 50, max 500)
      - offset: zero-based starting index (default 0)
    """
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500
    if offset < 0:
        offset = 0

    docs_path = _SUMMARY_INDEX_DIR / "summary_docs.json"
    if not docs_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"summary_docs.json not found at {docs_path}",
        )

    try:
        with open(docs_path, encoding="utf-8") as f:
            docs = json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load summary docs: {exc}")

    if not isinstance(docs, list):
        raise HTTPException(status_code=500, detail="Invalid summary_docs.json format; expected a list")

    total = len(docs)
    page = docs[offset: offset + limit]

    return {
        "index_dir": str(_SUMMARY_INDEX_DIR),
        "summary_docs_path": str(docs_path),
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "docs": page,
    }


@app.get("/api/ppt-text/{doc_id}")
def get_ppt_text(doc_id: str) -> dict:
    """Return the extracted and cleaned text from a PPT idea card."""
    try:
        svc = ValueStreamService()
        raw_text = svc.get_ppt_text(doc_id=doc_id)
        cleaned = clean_ppt_text(raw_text)
        return {
            "doc_id": doc_id,
            "raw_preview": raw_text[:2000],
            "cleaned_preview": cleaned[:2000],
            "char_count": len(raw_text),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search")
def run_search(req: SearchRequest) -> dict:
    """Run a vector-only search against the Azure index."""
    try:
        results = run_vector_search(query=req.query, top_k=req.top_k)
        return {"results": results or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/compare")
def run_comparison(req: ComparisonRequest) -> dict:
    """
    Compare predicted value streams against ground truth using fuzzy matching.

    Handles term variations like:
    - "Configure, Price, and Quote" vs "Configure Price and Quote"
    - "Manage Utilization Management Program" vs "Manage Utilization Management"
    - "Order to Cash for Group Coverage" vs "Order to Cash"
    """
    try:
        comparison = compare_value_streams(
            predicted=req.predicted,
            ground_truth=req.ground_truth,
            fuzzy_threshold=req.fuzzy_threshold
        )
        return comparison
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate")
def run_generate(req: GenerateRequest) -> dict:
    """
    Run the full pipeline:
    1. Extract PPT text (if doc_id provided) OR use query_text directly
    2. Build multiple retrieval views
    3. Vector search each view
    4. Aggregate candidates
    5. LLM selection

    Returns the full structured result including candidates, selection, and raw LLM response.
    """
    try:
        if req.doc_id:
            svc = ValueStreamService()
            raw_text = svc.get_ppt_text(doc_id=req.doc_id)
            query = raw_text
        elif req.query_text:
            query = req.query_text
        else:
            raise HTTPException(
                status_code=400, detail="Provide either doc_id or query_text"
            )

        approach = _normalize_approach(req.approach)
        if approach == "summary-rag":
            result = run_summary_rag_pipeline(
                ppt_text=query,
                allowed_value_stream_names=req.allowed_value_stream_names,
                top_analogs=5,
                top_kg_candidates=req.fetch_count,
                trace_mode=True,
            )
        elif approach == "rag":
            result = generate_value_streams_rag(
                ppt_text=query,
                allowed_value_stream_names=req.allowed_value_stream_names,
                top_k_per_view=req.fetch_count,
            )
        else:
            result = select_value_streams(query=query, fetch_count=req.fetch_count)

        result["approach"] = approach
        result = _normalize_for_ui(result)
        return _enrich_and_attach_ground_truth(result, req, ground_truth_source="ticket_chunks")

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        if _is_gateway_timeout_error(e):
            raise HTTPException(
                status_code=504,
                detail="Upstream gateway timeout (504). Request aborted without retries.",
            )
        raise HTTPException(status_code=500, detail=str(e))

# --- Historic RAG API Endpoint ---
@app.post("/api/historic-rag")
def run_historic_rag(req: GenerateRequest) -> dict:
    """
    Run the historical RAG pipeline on an idea card or query text.
    """
    try:
        if req.doc_id:
            svc = ValueStreamService()
            raw_text = svc.get_ppt_text(doc_id=req.doc_id)
            query = raw_text
        elif req.query_text:
            query = req.query_text
        else:
            raise HTTPException(status_code=400, detail="Provide either doc_id or query_text")

        result = run_historical_rag_pipeline(
            query,
            allowed_value_stream_names=req.allowed_value_stream_names,
            fetch_count=req.fetch_count,
            historical_faiss_dir=str(_HISTORICAL_FAISS_DIR),
        )

        result["approach"] = "historic-rag"
        result = _normalize_historic_rag_for_ui(result)
        return _enrich_and_attach_ground_truth(result, req, ground_truth_source="historical_faiss")

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        if _is_gateway_timeout_error(e):
            raise HTTPException(
                status_code=504,
                detail="Upstream gateway timeout (504). Request aborted without retries.",
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-rag")
def run_generate_rag(req: GenerateRequest) -> dict:
    """
    Run the dedicated RAG pipeline:
    1. Extract/clean text from idea card or query text
    2. Retrieve historical chunks from idp_idmt_data
    3. Map historical ticket evidence to local ticket->value-stream mappings
    4. Retrieve candidate value streams from idp_kg_data_test
    5. Constrained LLM selection using YAML prompts
    """
    try:
        if req.doc_id:
            svc = ValueStreamService()
            raw_text = svc.get_ppt_text(doc_id=req.doc_id)
            query = raw_text
        elif req.query_text:
            query = req.query_text
        else:
            raise HTTPException(status_code=400, detail="Provide either doc_id or query_text")

        result = generate_value_streams_rag(
            ppt_text=query,
            allowed_value_stream_names=req.allowed_value_stream_names,
            top_per_view=req.fetch_count,
        )
        result["approach"] = "rag"
        result = _normalize_for_ui(result)
        return _enrich_and_attach_ground_truth(result, req, ground_truth_source="ticket_chunks")

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        if _is_gateway_timeout_error(e):
            raise HTTPException(
                status_code=504,
                detail="Upstream gateway timeout (504). Request aborted without retries.",
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/generate-trace")
def run_generate_trace(req: GenerateRequest) -> dict:
    """
    Run the summary-RAG pipeline with full step-by-step trace.

    Returns the normal result PLUS a ``trace`` dict capturing every
    intermediate stage:
    - step1_input:           cleaned text preview
    - step1_summary:         new-card semantic summary (LLM output)
    - step2_faiss:           FAISS query text + analog ticket results
    - step3_vs_evidence:     value stream support aggregated from analogs
    - step4_kg_candidates:   KG candidates fetched + scored
    - step5_raw_evidence:    raw chunk snippets for top tickets
    - step6_llm_selection:   exact system + user prompt sent to GPT + raw response
    - step7_canonical_injection: which canonicals were injected vs LLM-selected
    """
    try:
        if req.doc_id:
            svc = ValueStreamService()
            raw_text = svc.get_ppt_text(doc_id=req.doc_id)
            query = raw_text
        elif req.query_text:
            query = req.query_text
        else:
            raise HTTPException(status_code=400, detail="Provide either doc_id or query_text")

        result = run_summary_rag_pipeline(
            ppt_text=query,
            allowed_value_stream_names=req.allowed_value_stream_names,
            top_analogs=5,
            top_kg_candidates=req.fetch_count,
            trace_mode=True,
        )

        result["approach"] = "summary-rag"
        result = _normalize_for_ui(result)
        return _enrich_and_attach_ground_truth(result, req, ground_truth_source="ticket_chunks")

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        if _is_gateway_timeout_error(e):
            raise HTTPException(
                status_code=504,
                detail="Upstream gateway timeout (504). Request aborted without retries.",
            )
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/summary-rag")
def run_summary_rag(req: GenerateRequest) -> dict:
    """
    Alias endpoint for clients that select `/summary-rag` directly.
    Executes the summary-RAG flow with trace.
    """
    req.approach = "summary-rag"
    return run_generate(req)
