from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from vs_app.container import TicketSourceFactory, normalize_ticket_source
from vs_app.modules.ingestion.service import IngestionService
from vs_app.modules.ingestion.summary.pipeline import ingest_ticket_summary_payload
from vs_app.modules.ingestion.chunks.pipeline import ingest_ticket_chunks_payload
from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.settings import Settings

logger = logging.getLogger(__name__)

_SOURCE_NAME_KEY = "_vs_api_source_name"


@dataclass(slots=True)
class ApiContainer:
    ingestion: IngestionService
    rag: ValueStreamRagService


class _AnnotatedTicketSourceFactory:
    def __init__(self, base_factory: TicketSourceFactory, config: Any = None) -> None:
        self._base_factory = base_factory
        self._config = config

    def build(self, source: str | None = None):
        source_name = normalize_ticket_source(source)
        session = self._base_factory.build(source=source_name)
        return _AnnotatedTicketSourceSession(session, source_name=source_name, config=self._config)


class _AnnotatedTicketSourceSession:
    def __init__(self, session: Any, *, source_name: str, config: Any = None) -> None:
        self._session = session
        self._source_name = source_name
        self._config = config

    async def __aenter__(self) -> "_AnnotatedTicketClient":
        client = await self._session.__aenter__()
        return _AnnotatedTicketClient(
            client,
            source_name=self._source_name,
            config=self._config,
        )

    async def __aexit__(self, *args: Any) -> Any:
        return await self._session.__aexit__(*args)


class _AnnotatedTicketClient:
    def __init__(self, client: Any, *, source_name: str, config: Any = None) -> None:
        self._client = client
        self._source_name = source_name
        self._config = config

    async def get_ticket(self, ticket_id: str) -> dict:
        payload = await self._client.get_ticket_data(ticket_id, config=self._config)
        if not isinstance(payload, dict):
            return payload
        ticket = dict(payload)
        ticket[_SOURCE_NAME_KEY] = self._source_name
        return ticket


class _SummaryPipelineAdapter:
    def __init__(self, base_factory: TicketSourceFactory, config: Any = None) -> None:
        self._base_factory = base_factory
        self._config = config

    async def run(self, ticket: dict) -> Any:
        payload = _strip_api_metadata(ticket)
        async with self._base_factory.build(source=_resolve_ticket_source_name(ticket)) as ticket_source:
            return await ingest_ticket_summary_payload(
                payload,
                ticket_source,
                None,
                None,
                self._config,
            )


class _ChunkPipelineAdapter:
    def __init__(self, base_factory: TicketSourceFactory, config: Any = None) -> None:
        self._base_factory = base_factory
        self._config = config

    async def run(self, ticket: dict) -> Any:
        payload = _strip_api_metadata(ticket)
        async with self._base_factory.build(source=_resolve_ticket_source_name(ticket)) as ticket_source:
            return await ingest_ticket_chunks_payload(
                payload,
                ticket_source,
                None,
                None,
                self._config,
            )


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

        # ticket_id maps to a local idea card file — read it from disk
        query_text = await asyncio.to_thread(self._read_idea_card, command.ticket_id)
        return await super().analyze(replace(command, query=query_text))

    @staticmethod
    def _read_idea_card(ticket_id: str) -> str:
        from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text
        return extract_idea_card_text(doc_id=ticket_id)


def _resolve_ticket_source_name(ticket: dict) -> str:
    return normalize_ticket_source(ticket.get(_SOURCE_NAME_KEY))


def _strip_api_metadata(ticket: dict) -> dict:
    payload = dict(ticket)
    payload.pop(_SOURCE_NAME_KEY, None)
    return payload


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

    ingestion = IngestionService(
        ticket_source_factory=_AnnotatedTicketSourceFactory(ticket_source_factory, config=config),
        summary_pipeline=_SummaryPipelineAdapter(ticket_source_factory, config=config),
        chunk_pipeline=_ChunkPipelineAdapter(ticket_source_factory, config=config),
        debug_writer=None,
    )
    rag = _ApiValueStreamRagService(
        ticket_source_factory=ticket_source_factory,
        config=config,
    )

    return ApiContainer(
        ingestion=ingestion,
        rag=rag,
    )
