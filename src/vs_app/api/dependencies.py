from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from vs_app.container import TicketSourceFactory, normalize_ticket_source
from vs_app.modules.ingestion.service import IngestionService
from vs_app.modules.ingestion.summary.pipeline import ingest_ticket_summary_payload
from vs_app.modules.ingestion.chunks.pipeline import ingest_ticket_chunks_payload
from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.modules.tickets.text_assembly import build_retrieval_text
from vs_app.modules.tickets.text_processing import clean_description, extract_comment_texts
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
        if command.idea_card_text or command.query or not command.ticket_id:
            return await super().analyze(command)

        query_text = await self._build_query_from_ticket(
            ticket_id=command.ticket_id,
            source=command.source,
        )
        adjusted_command = replace(command, query=query_text)
        return await super().analyze(adjusted_command)

    async def _build_query_from_ticket(
        self,
        *,
        ticket_id: str,
        source: str | None,
    ) -> str:
        source_name = normalize_ticket_source(source)
        async with self._ticket_source_factory.build(source=source_name) as ticket_source:
            ticket_data = await ticket_source.get_ticket_data(ticket_id, config=self._config)
            attachments = list(ticket_data.get("attachments", []) or [])
            attachment_texts: list[str] = []
            if attachments:
                try:
                    contents = await ticket_source.fetch_attachment_content(attachments)
                    attachment_texts = [
                        str(item.get("text_content") or "").strip()
                        for item in contents or []
                        if str(item.get("text_content") or "").strip() and not item.get("error")
                    ]
                except Exception as exc:
                    logger.warning("Attachment extraction unavailable for %s: %s", ticket_id, exc)

        fields = ticket_data.get("fields", {}) or {}
        description = clean_description(fields.get("description"))
        comment_texts = extract_comment_texts(fields.get("comment") or {})
        query_text = build_retrieval_text(
            title=str(fields.get("summary") or ticket_id),
            description_cleaned=description,
            attachment_texts=attachment_texts,
            comment_texts=comment_texts,
        )
        return query_text or str(fields.get("summary") or ticket_id)


def _resolve_ticket_source_name(ticket: dict) -> str:
    return normalize_ticket_source(ticket.get(_SOURCE_NAME_KEY))


def _strip_api_metadata(ticket: dict) -> dict:
    payload = dict(ticket)
    payload.pop(_SOURCE_NAME_KEY, None)
    return payload


def _build_ingestion_config() -> Any:
    try:
        from pipelines.jira_batch.config import JiraIngestionConfig

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
