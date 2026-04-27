from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.modules.rag.service import ValueStreamRagCommand

router = APIRouter(prefix="/rag", tags=["rag"])

_FAISS_DIR = Path(os.environ.get("HISTORICAL_FAISS_DIR", "ticket_data/_faiss"))


def _ground_truth_from_faiss(ticket_id: str) -> list[str]:
    docs_path = _FAISS_DIR / "summary_docs.json"
    if not docs_path.exists():
        return []
    try:
        docs = json.loads(docs_path.read_text(encoding="utf-8"))
        if isinstance(docs, dict):
            docs = docs.get("summaries") or []
        key = ticket_id.strip().lower()
        for doc in docs or []:
            if str(doc.get("ticket_id") or "").strip().lower() == key:
                names = (
                    doc.get("value_stream_names")
                    or doc.get("direct_vs_names")
                    or doc.get("value_stream_labels")
                    or []
                )
                return [str(n).strip() for n in names if str(n).strip()]
    except Exception:
        pass
    return []


@router.post("/value-streams", response_model=ValueStreamRagResponse)
async def predict_value_streams(
    request: ValueStreamRagRequest,
    container: ApiContainer = Depends(get_container),
) -> ValueStreamRagResponse:
    command = ValueStreamRagCommand(
        ticket_id=request.ticket_id,
        idea_card_text=request.idea_card_text,
        source=request.source,
        fetch_count=max(request.top_k_historical, request.top_k_value_streams),
        top_k_historical=request.top_k_historical,
        top_k_value_streams=request.top_k_value_streams,
        use_llm_finalizer=request.use_llm_finalizer,
    )
    result = await container.rag.analyze(command)
    response = ValueStreamRagResponse.from_result(result)
    if request.ticket_id:
        response.ground_truth = _ground_truth_from_faiss(request.ticket_id)
    return response
