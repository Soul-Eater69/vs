from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from src.pipelines import select_value_streams
from src.services.value_stream.value_stream_service import ValueStreamService

try:
    from vs.pipelines.historical_rag import run_historical_rag_pipeline
except ImportError:
    from pipelines.historical_rag import run_historical_rag_pipeline

from api.config import HISTORICAL_FAISS_DIR
from api.models import GenerateRequest
from api.utils import (
    enrich_and_attach_ground_truth,
    is_gateway_timeout_error,
    normalize_for_ui,
    normalize_historic_rag_for_ui,
)

router = APIRouter()


def _resolve_query(req: GenerateRequest) -> str:
    if req.doc_id:
        return ValueStreamService().get_ppt_text(doc_id=req.doc_id)
    if req.query_text:
        return req.query_text
    raise HTTPException(status_code=400, detail="Provide either doc_id or query_text")


def _handle_pipeline_error(e: Exception) -> None:
    traceback.print_exc()
    if is_gateway_timeout_error(e):
        raise HTTPException(
            status_code=504,
            detail="Upstream gateway timeout (504). Request aborted without retries.",
        )
    raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/generate")
def run_generate(req: GenerateRequest) -> dict:
    try:
        query = _resolve_query(req)
        result = select_value_streams(query=query, fetch_count=req.fetch_count)
        result["approach"] = "plain"
        return enrich_and_attach_ground_truth(normalize_for_ui(result), req, ground_truth_source="ticket_chunks")
    except HTTPException:
        raise
    except Exception as e:
        _handle_pipeline_error(e)


@router.post("/api/historic-rag")
def run_historic_rag(req: GenerateRequest) -> dict:
    try:
        query = _resolve_query(req)
        result = run_historical_rag_pipeline(
            query,
            allowed_value_stream_names=req.allowed_value_stream_names,
            fetch_count=req.fetch_count,
            historical_faiss_dir=str(HISTORICAL_FAISS_DIR),
        )
        result["approach"] = "historic-rag"
        return enrich_and_attach_ground_truth(normalize_historic_rag_for_ui(result), req, ground_truth_source="historical_faiss")
    except HTTPException:
        raise
    except Exception as e:
        _handle_pipeline_error(e)
