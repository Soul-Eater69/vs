"""
Simplified ingestion pipeline.

One function: fetch a Jira ticket → extract text → chunk → map value streams → return result.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ...ingestion.adapters.jira import get_ticket_data_compat
from .extract import (
    build_chunks,
    extract_attachment_texts,
    extract_comments,
    extract_description,
    extract_metadata,
)
from ...ingestion.adapters.jira.value_stream.value_stream_mapping import (
    resolve_value_stream_mapping,
    resolve_value_stream_epics_mapping,
)
from ...ingestion.application.processing.metadata import classify_links

logger = logging.getLogger(__name__)


async def ingest_ticket(
    ticket_id: str,
    jira_client: Any,
    llm_client: Optional[Any] = None,
    embedding_client: Optional[Any] = None,
    sharepoint_client: Optional[Any] = None,
    skip_llm: bool = False,
    skip_embeddings: bool = False,
) -> dict:
    """
    Ingest a single Jira ticket. Returns a flat document with:
      - ticket_key, metadata, chunks, value_stream mapping, summary, embeddings
    """
    # 1) Fetch from Jira
    ticket_data = await get_ticket_data_compat(
        jira_client,
        ticket_id,
        llm_client=llm_client,
    )
    fields = ticket_data.get("fields", {})

    # 2) Extract text
    meta = extract_metadata(fields, ticket_id)
    description = extract_description(fields)
    comments = extract_comments(fields)

    # 3) Extract attachment text (best-effort, with SharePoint fallback)
    attachments = ticket_data.get("attachments", []) or []
    prefetched: dict[str, bytes] = {}
    for att in attachments:
        att_id = str(att.get("id") or att.get("filename") or "")
        content_url = str(att.get("content") or att.get("url") or "")
        try:
            prefetched[att_id] = await jira_client.download_attachment(att)
        except Exception as exc:
            # Fallback: if it's a SharePoint URL and we have a SharePoint client
            if sharepoint_client and "sharepoint.com" in content_url:
                try:
                    sp_bytes = sharepoint_client.get_file_content_by_url(content_url)
                    if sp_bytes:
                        prefetched[att_id] = sp_bytes
                        logger.info("SharePoint fallback succeeded for %s", att.get("filename"))
                    else:
                        logger.warning("SharePoint fallback returned empty for %s", att.get("filename"))
                except Exception as sp_exc:
                    logger.warning("SharePoint fallback failed for %s: %s", att.get("filename"), sp_exc)
            else:
                logger.warning("Download failed for %s: %s", att.get("filename"), exc)

    def download_fn(att: dict) -> bytes:
        att_id = str(att.get("id") or att.get("filename") or "")
        data = prefetched.get(att_id)
        if data is None:
            raise RuntimeError(f"Not prefetched: {att.get('filename')}")
        return data

    attachment_texts = extract_attachment_texts(attachments, download_fn) if prefetched else []

    # 4) Chunk
    chunks = build_chunks(
        ticket_key=ticket_id,
        description=description,
        comments=comments,
        attachment_texts=attachment_texts,
    )

    # 5) Value stream + epics mapping
    classified_links = classify_links(fields.get("issuelinks", []))
    vs_mapping = resolve_value_stream_mapping(ticket_data, classified_links, llm_client=llm_client)
    vs_epics = resolve_value_stream_epics_mapping(ticket_data, classified_links, llm_client=llm_client)

    # 6) Summary (LLM or heuristic)
    summary = _generate_summary(
        description=description,
        meta=meta,
        chunks=chunks,
        llm_client=llm_client if not skip_llm else None,
    )

    # 7) Embeddings
    if embedding_client and not skip_embeddings:
        _embed_chunks(chunks, summary, embedding_client)

    # 8) Assemble document
    return {
        "ticket_key": ticket_id,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "metadata": meta,
        "description": description,
        "comments": comments,
        "summary": summary,
        "chunks": chunks,
        "value_streams": {
            "ids": vs_mapping.get("vs_ids", []),
            "names": vs_mapping.get("vs_names", []),
            "statuses": vs_mapping.get("vs_statuses", []),
            "linked": vs_mapping.get("linked_value_streams", []),
            "label_source": vs_mapping.get("label_source", ""),
        },
        "epics": {
            "ids": vs_epics.get("all_epic_ids", []),
            "names": vs_epics.get("all_epic_names", []),
            "items": vs_epics.get("epics_normalized", []),
            "vs_with_epics": vs_epics.get("vs_with_epics", []),
        },
        "attachment_count": len(attachments),
        "attachments_extracted": len(attachment_texts),
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _generate_summary(
    description: str,
    meta: dict,
    chunks: list[dict],
    llm_client: Optional[Any] = None,
) -> str:
    """Generate a summary using LLM if available, else heuristic."""
    if llm_client is not None:
        try:
            return _llm_summary(meta, chunks, llm_client)
        except Exception as exc:
            logger.warning("LLM summary failed: %s", exc)

    # Heuristic: title + first ~200 words of description
    title = meta.get("title", "")
    desc_words = description.split()[:200]
    return f"{title}. {' '.join(desc_words)}".strip() if desc_words else title


def _llm_summary(meta: dict, chunks: list[dict], llm_client: Any) -> str:
    """Call LLM for a concise ticket summary."""
    top_chunks = sorted(chunks, key=lambda c: c.get("word_count", 0), reverse=True)[:6]
    chunk_text = "\n---\n".join(c["text"][:1000] for c in top_chunks)

    prompt = (
        f"Summarize this Jira ticket in 2-3 sentences.\n\n"
        f"Title: {meta.get('title', '')}\n"
        f"Type: {meta.get('issue_type', '')}\n"
        f"Status: {meta.get('status', '')}\n\n"
        f"Content:\n{chunk_text}"
    )

    response = llm_client.chat.completions.create(
        model=getattr(llm_client, "model", "gpt-5-mini-idp"),
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

def _embed_chunks(chunks: list[dict], summary: str, embedding_client: Any) -> None:
    """Embed summary + all chunks in-place."""
    from ...clients.embedding import embed_batch

    texts = [summary] + [c["text"] for c in chunks]
    try:
        embeddings = embed_batch(texts, embedding_client)
    except Exception as exc:
        logger.warning("Embedding failed: %s", exc)
        return

    for i, chunk in enumerate(chunks):
        chunk["embedding"] = embeddings[i + 1] if (i + 1) < len(embeddings) else []


# ---------------------------------------------------------------------------
# Projection helpers (for output compatibility)
# ---------------------------------------------------------------------------

def project_chunk_records(ticket_id: str, result: dict, jira_base_url: str = "") -> list[dict]:
    """Convert pipeline result to chunk records for search index."""
    meta = result.get("metadata", {})
    source_url = f"{jira_base_url.rstrip('/')}/browse/{ticket_id}" if jira_base_url else ticket_id

    records = []
    for chunk in result.get("chunks", []):
        records.append({
            "id": chunk.get("chunk_uid", chunk.get("chunk_id", "")),
            "content": chunk.get("text", ""),
            "contentVector": chunk.get("embedding"),
            "dataSource": "jira",
            "sourceId": ticket_id,
            "sourceUrl": source_url,
            "title": meta.get("title", ticket_id),
            "contentType": chunk.get("source", "unknown").split(":")[0],
            "tokenCount": chunk.get("word_count", 0),
            "project": ticket_id.split("-")[0] if "-" in ticket_id else "",
            "issueType": meta.get("issue_type", ""),
            "status": meta.get("status", ""),
            "priority": meta.get("priority", ""),
            "createdDate": meta.get("created", ""),
        })
    return records


def project_vs_record(ticket_id: str, result: dict) -> dict:
    """Convert pipeline result to value stream record."""
    meta = result.get("metadata", {})
    vs = result.get("value_streams", {})

    return {
        "id": ticket_id,
        "ticketId": ticket_id,
        "project": ticket_id.split("-")[0] if "-" in ticket_id else "",
        "title": meta.get("title", ticket_id),
        "valueStreamIds": vs.get("ids", []),
        "valueStreamNames": vs.get("names", []),
        "valueStreamStatuses": vs.get("statuses", []),
        "labelSource": vs.get("label_source", ""),
    }
