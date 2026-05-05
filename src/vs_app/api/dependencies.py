from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from dataclasses import replace
from functools import lru_cache
from typing import Any

from vs_app.container import TicketSourceFactory
from vs_app.modules.ingestion.summary.pipeline import ingest_ticket_summary
from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApiContainer:
    ingestion: Any
    rag: ValueStreamRagService


@dataclass(slots=True)
class _ApiIngestResult:
    ticket_id: str
    summary: dict | None
    errors: list[str]


class _ApiSummaryService:
    def __init__(
        self,
        *,
        ticket_source_factory: TicketSourceFactory,
        config: Any = None,
    ) -> None:
        self._ticket_source_factory = ticket_source_factory
        self._config = config

    async def ingest_ticket(self, *, ticket_id: str, source: str = "jira") -> _ApiIngestResult:
        async with self._ticket_source_factory.build(source=source) as ticket_source:
            doc = await ingest_ticket_summary(
                ticket_key=ticket_id,
                jira_client=ticket_source,
                llm_client=None,
                embedding_client=None,
                cfg=self._config,
            )
        return _ApiIngestResult(ticket_id=ticket_id, summary=doc.to_index_doc(), errors=[])


class _ApiValueStreamRagService(ValueStreamRagService):
    def __init__(
        self,
        *,
        ticket_source_factory: TicketSourceFactory,
        config: Any = None,
    ) -> None:
        super().__init__()
        self._ticket_source_factory = ticket_source_factory
        self._config = config

    async def analyze(self, command: ValueStreamRagCommand):
        if command.idea_card_text or command.query:
            return await super().analyze(command)

        if not command.ticket_id:
            return await super().analyze(command)

        query_text = await asyncio.to_thread(self._read_idea_card, command.ticket_id)
        return await super().analyze(replace(command, query=query_text))

    @staticmethod
    def _read_idea_card(ticket_id: str) -> str:
        from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text

        return extract_idea_card_text(doc_id=ticket_id)


def _build_ingestion_config() -> Any:
    try:
        from vs_app.jobs.jira_batch.config import JiraIngestionConfig

        return JiraIngestionConfig()
    except Exception as exc:
        logger.warning("Falling back to default ingestion config wiring: %s", exc)
        return None


@lru_cache(maxsize=1)
def get_container() -> ApiContainer:
    settings = Settings.from_env()
    config = _build_ingestion_config()
    ticket_source_factory = TicketSourceFactory(settings)

    return ApiContainer(
        ingestion=_ApiSummaryService(
            ticket_source_factory=ticket_source_factory,
            config=config,
        ),
        rag=_ApiValueStreamRagService(
            ticket_source_factory=ticket_source_factory,
            config=config,
        ),
    )
