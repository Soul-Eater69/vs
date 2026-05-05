from __future__ import annotations

from fastapi import APIRouter, Depends

from vs_app.api.dependencies import ApiContainer, get_container
from vs_app.api.schemas.ingestion_requests import IngestTicketRequest
from vs_app.api.schemas.ingestion_responses import IngestTicketResponse

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/tickets/{ticket_id}", response_model=IngestTicketResponse)
async def ingest_ticket(
    ticket_id: str,
    request: IngestTicketRequest,
    container: ApiContainer = Depends(get_container),
) -> IngestTicketResponse:
    result = await container.ingestion.ingest_ticket(
        ticket_id=ticket_id,
        source=request.source,
    )
    return IngestTicketResponse.from_result(result)
