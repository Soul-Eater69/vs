from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from vs_app import settings as config
from vs_app.integrations.clients.embedding import EmbeddingClient
from vs_app.ingestion.summary.mapper import format_structured_summary_text

logger = logging.getLogger(__name__)

HISTORICAL_SELECT_FIELDS = [
    "id",
    "ticket_id",
    "content",
    "summary_text",
    "business_problem",
    "business_capability",
    "value_stream_names",
    "value_stream_ids",
    "jira_group_ids",
    "direct_vs_names",
    "implied_vs_names",
    "value_streams_json",
    "label_source",
]


def upload_historical_summary_index(
    *,
    summaries: list[dict] | None = None,
    output_dir: str | Path = "ticket_data",
    index_name: str = config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    embedding: EmbeddingClient | None = None,
    create_index: bool = False,
    recreate_index: bool = False,
    reset_index: bool = False,
    document_action: str = "upload",
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Upload ingested historical ticket summaries to Azure AI Search."""
    summary_rows = list(summaries) if summaries is not None else load_summary_artifacts(Path(output_dir))
    embedding_client = embedding or EmbeddingClient()

    docs, skipped = build_historical_azure_documents(
        summary_rows,
        embedding=embedding_client,
    )
    if recreate_index:
        recreate_historical_summary_index(
            index_name=index_name,
            vector_dimensions=getattr(embedding_client, "dimension", config.EMBEDDING_DIMENSION),
        )
    elif create_index:
        ensure_historical_summary_index(
            index_name=index_name,
            vector_dimensions=getattr(embedding_client, "dimension", config.EMBEDDING_DIMENSION),
        )

    deleted_count = 0
    if reset_index:
        deleted_count = clear_historical_summary_index(index_name=index_name)

    gateway_response: dict[str, Any] = {}
    if docs:
        gateway_response = send_historical_documents(
            index_name=index_name,
            documents=docs,
            action=document_action,
            batch_size=batch_size,
        )

    logger.info(
        "[AZURE] Uploaded %d historical summaries to %s; skipped %d",
        len(docs),
        index_name,
        len(skipped),
    )
    return {
        "index_name": index_name,
        "source_summary_count": len(summary_rows),
        "summary_doc_count": len(docs),
        "recreated_index": recreate_index,
        "deleted_doc_count": deleted_count,
        "document_action": document_action,
        "gateway_response": gateway_response,
        "skipped_summary_count": len(skipped),
        "skipped_summary_docs": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def send_historical_documents(
    *,
    index_name: str,
    documents: list[dict],
    action: str = "upload",
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Send historical documents through the IDP AI Search documents gateway."""
    normalized = action.strip().lower()
    try:
        client = _make_documents_client()
        if normalized == "upload":
            return client.upload_documents(
                index_name=index_name,
                documents=documents,
                batch_size=batch_size,
            )
        if normalized == "update":
            return client.update_documents(
                index_name=index_name,
                documents=documents,
                batch_size=batch_size,
            )
        raise ValueError("document_action must be 'upload' or 'update'")
    except Exception as exc:
        if normalized not in {"upload", "update"}:
            raise
        logger.warning(
            "AI Search documents gateway failed; falling back to direct Azure client: %s",
            exc,
        )
        return _send_historical_documents_direct(
            index_name=index_name,
            documents=documents,
            action=normalized,
            gateway_error=exc,
        )


def _send_historical_documents_direct(
    *,
    index_name: str,
    documents: list[dict],
    action: str,
    gateway_error: Exception,
) -> dict[str, Any]:
    """Fallback upload through the direct Azure Search SDK client."""
    client = _make_search_client(index_name=index_name)
    if action == "update" and hasattr(client, "merge_or_upload_documents"):
        raw_result = client.merge_or_upload_documents(documents)
        fallback_action = "merge_or_upload_documents"
    else:
        raw_result = client.upload_documents(documents)
        fallback_action = "upload_documents"

    result_count, failed_count = _indexing_result_counts(raw_result)
    return {
        "success": failed_count == 0,
        "fallback_used": True,
        "fallback_client": "AzureDirectSearchClient",
        "fallback_action": fallback_action,
        "gateway_error": str(gateway_error),
        "document_count": len(documents),
        "result_count": result_count,
        "failed_count": failed_count,
    }


def clear_historical_summary_index(
    *,
    index_name: str = config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    client: Any | None = None,
) -> int:
    """Delete all documents from the Azure historical summary index."""
    search_client = client or _make_search_client(index_name=index_name)
    rows = search_client.search_all(search_text="*", select=["id"])
    delete_docs = [{"id": str(row.get("id") or "")} for row in rows if str(row.get("id") or "")]
    if delete_docs:
        search_client.delete_documents(delete_docs)
    return len(delete_docs)


def load_summary_artifacts(output_dir: Path) -> list[dict]:
    """Load summary artifacts produced by jobs/ingest_tickets.py."""
    for aggregate_path in (output_dir / "summaries.json", output_dir / "_all_summaries.json"):
        if aggregate_path.exists():
            payload = _read_json(aggregate_path)
            if isinstance(payload, dict):
                return [row for row in (payload.get("summaries") or []) if isinstance(row, dict)]
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]

    docs: list[dict] = []
    for path in sorted(output_dir.glob("*/summary.json")):
        payload = _read_json(path)
        if isinstance(payload, dict):
            docs.append(payload)
    return docs


def build_historical_azure_documents(
    summaries: list[dict],
    *,
    embedding: EmbeddingClient,
) -> tuple[list[dict], list[dict]]:
    """Map ingested summary docs to the Azure historical-search schema."""
    docs: list[dict] = []
    skipped: list[dict] = []

    for index, row in enumerate(summaries):
        ticket_id = str(row.get("ticket_id") or row.get("key") or "").strip()
        content = format_structured_summary_text(row).strip()
        if not ticket_id or not content:
            skipped.append(
                {
                    "ticket_id": ticket_id,
                    "index": index,
                    "reason": "missing_ticket_id_or_content",
                }
            )
            continue

        docs.append(
            {
                "id": _safe_key(ticket_id),
                "ticket_id": ticket_id,
                "content": content,
                "content_vector": _summary_vector(row) or embedding.embed(content),
                "summary_text": str(row.get("summary_text") or ""),
                "business_problem": str(row.get("business_problem") or ""),
                "business_capability": str(row.get("business_capability") or ""),
                "key_terms": _text_list(row.get("key_terms")),
                "stakeholders": _text_list(row.get("stakeholders")),
                "systems_and_products": _text_list(row.get("systems_and_products")),
                "value_stream_names": _text_list(row.get("value_stream_names")),
                "value_stream_ids": _text_list(row.get("value_stream_ids")),
                "jira_group_ids": _text_list(row.get("jira_group_ids")),
                "direct_vs_names": _text_list(row.get("direct_vs_names")),
                "implied_vs_names": _text_list(row.get("implied_vs_names")),
                "value_streams_json": _value_streams_json(row),
                "label_source": str(row.get("label_source") or ""),
            }
        )

    return docs, skipped


def search_historical_summaries(
    query: str,
    *,
    index_name: str = config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    top_k: int = 12,
    exclude_ticket_ids: Iterable[str] | None = None,
) -> list[dict]:
    """Search historical summaries in Azure AI Search and return RAG ticket hits."""
    excluded = {str(ticket_id or "").strip().upper() for ticket_id in (exclude_ticket_ids or [])}
    fetch_k = top_k + len(excluded)
    client = _make_search_client(index_name=index_name)
    rows = client.search_hybrid(
        query,
        top_k=fetch_k,
        vector_field="content_vector",
        select=HISTORICAL_SELECT_FIELDS,
        search_fields=["content", "summary_text", "business_problem", "business_capability"],
        use_semantic_rerank=False,
    )

    hits: list[dict] = []
    for row in rows:
        ticket_id = str(row.get("ticket_id") or "").strip()
        if not ticket_id or ticket_id.upper() in excluded:
            continue
        hits.append(_azure_row_to_ticket_hit(row))
        if len(hits) >= top_k:
            break
    return hits


def ensure_historical_summary_index(
    *,
    index_name: str = config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    vector_dimensions: int = config.EMBEDDING_DIMENSION,
) -> None:
    """Create or extend the Azure AI Search index for historical summaries."""
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import ClientSecretCredential
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SearchableField,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    credential = ClientSecretCredential(
        tenant_id=config.AZURE_TENANT_ID,
        client_id=config.AZURE_CLIENT_ID,
        client_secret=config.AZURE_CLIENT_SECRET,
    )
    index_client = SearchIndexClient(endpoint=config.AZURE_SEARCH_ENDPOINT, credential=credential)

    collection_string = SearchFieldDataType.Collection(SearchFieldDataType.String)
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="ticket_id", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=vector_dimensions,
            vector_search_profile_name="historical-vector-profile",
        ),
        SearchableField(name="summary_text", type=SearchFieldDataType.String),
        SearchableField(name="business_problem", type=SearchFieldDataType.String),
        SearchableField(name="business_capability", type=SearchFieldDataType.String),
        SearchField(name="key_terms", type=collection_string, filterable=True),
        SearchField(name="stakeholders", type=collection_string, filterable=True),
        SearchField(name="systems_and_products", type=collection_string, filterable=True),
        SearchField(name="value_stream_names", type=collection_string, filterable=True),
        SearchField(name="value_stream_ids", type=collection_string, filterable=True),
        SearchField(name="jira_group_ids", type=collection_string, filterable=True),
        SearchField(name="direct_vs_names", type=collection_string, filterable=True),
        SearchField(name="implied_vs_names", type=collection_string, filterable=True),
        SearchableField(name="value_streams_json", type=SearchFieldDataType.String),
        SimpleField(name="label_source", type=SearchFieldDataType.String, filterable=True),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="historical-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="historical-vector-profile",
                algorithm_configuration_name="historical-hnsw",
            )
        ],
    )
    try:
        existing = index_client.get_index(index_name)
    except ResourceNotFoundError:
        index_client.create_index(
            SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
        )
        return

    existing_field_names = {field.name for field in existing.fields}
    missing_fields = [field for field in fields if field.name not in existing_field_names]
    if not missing_fields:
        return
    existing.fields.extend(missing_fields)
    if existing.vector_search is None:
        existing.vector_search = vector_search
    index_client.create_or_update_index(existing)


def recreate_historical_summary_index(
    *,
    index_name: str = config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    vector_dimensions: int = config.EMBEDDING_DIMENSION,
) -> None:
    """Delete and recreate the historical summary index with only our schema."""
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import ClientSecretCredential
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchField,
        SearchFieldDataType,
        SearchIndex,
        SearchableField,
        SimpleField,
        VectorSearch,
        VectorSearchProfile,
    )

    credential = ClientSecretCredential(
        tenant_id=config.AZURE_TENANT_ID,
        client_id=config.AZURE_CLIENT_ID,
        client_secret=config.AZURE_CLIENT_SECRET,
    )
    index_client = SearchIndexClient(endpoint=config.AZURE_SEARCH_ENDPOINT, credential=credential)
    try:
        index_client.delete_index(index_name)
    except ResourceNotFoundError:
        pass

    collection_string = SearchFieldDataType.Collection(SearchFieldDataType.String)
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True),
        SimpleField(name="ticket_id", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=vector_dimensions,
            vector_search_profile_name="historical-vector-profile",
        ),
        SearchableField(name="summary_text", type=SearchFieldDataType.String),
        SearchableField(name="business_problem", type=SearchFieldDataType.String),
        SearchableField(name="business_capability", type=SearchFieldDataType.String),
        SearchField(name="key_terms", type=collection_string, filterable=True),
        SearchField(name="stakeholders", type=collection_string, filterable=True),
        SearchField(name="systems_and_products", type=collection_string, filterable=True),
        SearchField(name="value_stream_names", type=collection_string, filterable=True),
        SearchField(name="value_stream_ids", type=collection_string, filterable=True),
        SearchField(name="jira_group_ids", type=collection_string, filterable=True),
        SearchField(name="direct_vs_names", type=collection_string, filterable=True),
        SearchField(name="implied_vs_names", type=collection_string, filterable=True),
        SearchableField(name="value_streams_json", type=SearchFieldDataType.String),
        SimpleField(name="label_source", type=SearchFieldDataType.String, filterable=True),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="historical-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="historical-vector-profile",
                algorithm_configuration_name="historical-hnsw",
            )
        ],
    )
    index_client.create_index(
        SearchIndex(name=index_name, fields=fields, vector_search=vector_search)
    )


def _azure_row_to_ticket_hit(row: dict) -> dict:
    return {
        "ticket_id": str(row.get("ticket_id") or ""),
        "best_score": float(row.get("@search.score", 0.0) or 0.0),
        "title": str(row.get("ticket_id") or ""),
        "summary_preview": str(row.get("content") or "")[:320],
        "value_stream_names": _text_list(row.get("value_stream_names")),
        "value_stream_ids": _text_list(row.get("value_stream_ids")),
        "jira_group_ids": _text_list(row.get("jira_group_ids")),
        "direct_vs_names": _text_list(row.get("direct_vs_names")),
        "implied_vs_names": _text_list(row.get("implied_vs_names")),
        "value_streams_json": str(row.get("value_streams_json") or ""),
        "label_source": str(row.get("label_source") or ""),
    }


def _make_search_client(**kwargs: Any) -> Any:
    from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient

    return AzureDirectSearchClient(**kwargs)


def _make_documents_client() -> Any:
    from vs_app.integrations.clients.aisearch_documents import AISearchDocumentsClient

    return AISearchDocumentsClient()


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _safe_key(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _summary_vector(row: dict) -> list[float]:
    values = row.get("summary_embedding")
    if not isinstance(values, list) or not values:
        return []
    out: list[float] = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            return []
    return out


def _value_streams_json(row: dict) -> str:
    value_streams = row.get("value_streams")
    if isinstance(value_streams, list):
        rows = [
            {
                "vs_id": str(item.get("vs_id") or "").strip(),
                "vs_name": str(item.get("vs_name") or "").strip(),
                "jira_group_id": str(item.get("jira_group_id") or "").strip(),
                "inference_type": str(item.get("inference_type") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
            }
            for item in value_streams
            if isinstance(item, dict) and str(item.get("vs_name") or "").strip()
        ]
        if rows:
            return json.dumps(rows, ensure_ascii=False)

    return str(row.get("value_streams_json") or "")


def _indexing_result_counts(raw_result: Any) -> tuple[int, int]:
    if raw_result is None:
        return 0, 0
    try:
        rows = list(raw_result)
    except TypeError:
        rows = [raw_result]

    failed = 0
    for row in rows:
        succeeded = getattr(row, "succeeded", None)
        if succeeded is None and isinstance(row, dict):
            succeeded = row.get("succeeded")
        if succeeded is False:
            failed += 1
    return len(rows), failed


__all__ = [
    "HISTORICAL_SELECT_FIELDS",
    "build_historical_azure_documents",
    "clear_historical_summary_index",
    "ensure_historical_summary_index",
    "load_summary_artifacts",
    "recreate_historical_summary_index",
    "search_historical_summaries",
    "send_historical_documents",
    "upload_historical_summary_index",
]
