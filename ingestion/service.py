import logging
from dataclasses import dataclass
from typing import Any, Optional

from ingestion.pipeline import IngestionMode, IngestionResult, ingest_ticket_payload

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TicketIngestionContext:
    jira_client: Any
    llm_client: Any | None = None
    embedding_client: Any | None = None


IngestionDeps = TicketIngestionContext


async def ingest_single_ticket(
    ticket_id: str,
    deps: TicketIngestionContext,
    cfg: Any,
    storage_dir: Optional[str] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    from jira import JiraTicketExtractionService

    extraction_service = JiraTicketExtractionService(jira_client=deps.jira_client)
    ticket_data = await extraction_service.extract_ticket(ticket_id, cfg)

    if not isinstance(ticket_data, dict):
        raise RuntimeError(
            f"Ticket {ticket_id} extraction did not return a dict. Got: {type(ticket_data)}"
        )
    if "key" not in ticket_data:
        raise RuntimeError(f"Ticket {ticket_id} extraction missing 'key'.")

    if storage_dir is not None:
        logger.warning(
            "storage_dir=%s is ignored — artifact persistence is handled by the pipeline config",
            storage_dir,
        )

    return await ingest_ticket_payload(
        ticket_data=ticket_data,
        jira_client=deps.jira_client,
        llm_client=deps.llm_client,
        embedding_client=deps.embedding_client,
        cfg=cfg,
        mode=mode,
    )


async def ingest_one_ticket(
    ticket_id: str,
    deps: TicketIngestionContext,
    cfg: Any,
    storage_dir: Optional[str] = None,
    mode: IngestionMode = "summary",
) -> IngestionResult:
    return await ingest_single_ticket(
        ticket_id=ticket_id,
        deps=deps,
        cfg=cfg,
        storage_dir=storage_dir,
        mode=mode,
    )
