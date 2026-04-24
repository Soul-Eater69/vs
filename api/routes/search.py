from __future__ import annotations

import traceback

from fastapi import APIRouter, HTTPException

from core import compare_value_streams
from src.pipelines import run_vector_search

from api.models import ComparisonRequest, SearchRequest

router = APIRouter()


@router.post("/api/search")
def run_search(req: SearchRequest) -> dict:
    try:
        results = run_vector_search(query=req.query, top_k=req.top_k)
        return {"results": results or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/compare")
def run_comparison(req: ComparisonRequest) -> dict:
    try:
        return compare_value_streams(
            predicted=req.predicted,
            ground_truth=req.ground_truth,
            fuzzy_threshold=req.fuzzy_threshold,
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
