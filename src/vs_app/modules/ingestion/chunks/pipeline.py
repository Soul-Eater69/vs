from __future__ import annotations

import logging
from typing import Any, Optional

from vs_app.integrations.embeddings.client import embed_batch
from vs_app.integrations.jira.fetch_compat import get_ticket_data_compat
from vs_app.modules.tickets.documents import HierarchicalTicketResult

from .chunk_builder import build_from_attachments, build_from_ticket_body

logger = logging.getLogger(__name__)


async def ingest_ticket_chunks(
    ticket_key: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> HierarchicalTicketResult:
    """Full chunk pipeline for a single ticket."""
    cfg = _default_cfg(cfg)
    ticket_data = await get_ticket_data_compat(
        jira_client,
        ticket_key,
        config=cfg,
        llm_client=llm_client,
    )
    return await ingest_ticket_chunks_payload(
        ticket_data=ticket_data,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
    )


async def ingest_ticket_chunks_payload(
    ticket_data: dict,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    cfg: Optional[Any] = None,
) -> HierarchicalTicketResult:
    cfg = _default_cfg(cfg)
    ticket_key = str(ticket_data.get("key", ""))

    vs_ids = list(ticket_data.get("value_stream_ids") or [])
    vs_names = list(ticket_data.get("value_stream_names") or [])
    label_source = str(
        ticket_data.get("value_stream_label_source")
        or ticket_data.get("label_source")
        or "jira_issuelinks"
    )

    sections, leaves, attachment_debug = await build_from_attachments(
        ticket_key=ticket_key,
        ticket_data=ticket_data,
        jira_client=jira_client,
        cfg=cfg,
    )

    body_fallback_used = False
    body_fallback_reason = ""
    if not leaves:
        body_fallback_used = True
        body_fallback_reason = (
            "no_attachment_leaves"
            if (attachment_debug.get("attachment_count_total") or 0) > 0
            else "no_attachments"
        )
        sections, leaves = build_from_ticket_body(
            ticket_key=ticket_key,
            ticket_data=ticket_data,
            llm_client=llm_client,
            cfg=cfg,
        )

    for doc in sections + leaves:
        doc.value_stream_ids = list(vs_ids)
        doc.value_stream_names = list(vs_names)

    if embedding_client is not None and (sections or leaves):
        _embed_chunks(sections + leaves, embedding_client, cfg)

    return HierarchicalTicketResult(
        ticket_id=ticket_key,
        value_stream_ids=list(vs_ids),
        value_stream_names=list(vs_names),
        label_source=label_source,
        sections=sections,
        chunks=leaves,
        debug={
            "chunk_source": "ticket_body" if body_fallback_used and leaves else ("attachments" if leaves else "none"),
            "body_fallback_used": body_fallback_used,
            "body_fallback_reason": body_fallback_reason,
            "attachment_processing": attachment_debug,
        },
    )


def _embed_chunks(docs: list, embedding_client: Any, cfg: Any) -> None:
    texts = [doc.text for doc in docs]
    try:
        vectors = embed_batch(
            texts,
            embedding_client,
            model=getattr(cfg, "embedding_model", None),
        )
    except Exception as exc:
        logger.warning("Embedding failed for %d chunks: %s", len(texts), exc)
        return

    for doc, vector in zip(docs, vectors or []):
        doc.embedding = list(vector or [])


def _default_cfg(cfg: Optional[Any]) -> Any:
    if cfg is not None:
        return cfg
    from vs_app.jobs.jira_batch.config import JiraIngestionConfig

    return JiraIngestionConfig()


__all__ = ["ingest_ticket_chunks", "ingest_ticket_chunks_payload"]
