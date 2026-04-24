from __future__ import annotations

from fastapi import APIRouter, Depends

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.rag_requests import ValueStreamRagRequest
from vs_app.api.schemas.rag_responses import ValueStreamRagResponse
from vs_app.modules.rag.service import ValueStreamRagCommand

router = APIRouter(prefix="/rag", tags=["rag"])


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
    return ValueStreamRagResponse.from_result(result)
