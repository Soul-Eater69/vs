from dataclasses import dataclass
from typing import Any, Optional

from ingestion.pipeline import ingest_ticket, ingest_ticket_payload


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
) -> dict:
    if storage_dir is not None and "storage_dir" in inspect.signature(ingest_ticket).parameters:
        kwargs: dict[str, Any] = {
            "ticket_key": ticket_id,
            "jira_client": deps.jira_client,
            "config": cfg,
            "llm_client": deps.llm_client,
            "embedding_client": deps.embedding_client,
            "storage_dir": storage_dir,
        }
        return await ingest_ticket(**kwargs)

    from jira import JiraTicketExtractionService
    extraction_service = JiraTicketExtractionService(jira_client=deps.jira_client)
    ticket_data = await extraction_service.extract_ticket(ticket_id, cfg)
    print(f"\n[DEBUG] Extracted ticket data for {ticket_id}: {ticket_data}\n", flush=True)
    if not isinstance(ticket_data, dict):
        raise RuntimeError(f"Ticket {ticket_id} extraction did not return a dict. Got: {type(ticket_data)} Value: {repr(ticket_data)}")
    if 'key' not in ticket_data:
        raise RuntimeError(
            f"Ticket {ticket_id} extraction missing 'key'. Raw data: {repr(ticket_data)}"
        )
    return await ingest_ticket_payload(
        ticket_data=ticket_data,
        jira_client=deps.jira_client,
        llm_client=deps.llm_client,
        embedding_client=deps.embedding_client,
        dict_path=getattr(cfg, "entity_dict_path", None),
        config=cfg,
    )


async def ingest_one_ticket(
    ticket_id: str,
    deps: TicketIngestionContext,
    cfg: Any,
    storage_dir: Optional[str] = None,
) -> dict:
    return await ingest_single_ticket(ticket_id=ticket_id, deps=deps, cfg=cfg, storage_dir=storage_dir)