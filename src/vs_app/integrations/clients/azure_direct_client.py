"""
Azure AI Search - direct client (no IDP gateway).

Bypasses the IDP search gateway entirely: authenticates via a service-principal
(ClientSecretCredential), vectorises the query locally with EmbeddingClient,
and submits the search request directly to the Azure AI Search REST endpoint
using the `azure-search-documents` SDK.

Usage
-----
from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient

client = AzureDirectSearchClient()

# Pure vector search
results = client.search_vector("provider contracting", top_k=5)

# Hybrid: BM25 + vector + semantic re-rank
results = client.search_hybrid("risk adjustment", top_k=5)

for doc in results:
    print(doc["entity_name"], doc["@search.score"])
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

from vs_app import settings as config
from .embedding import EmbeddingClient

logger = logging.getLogger(__name__)

_DEFAULT_SELECT_FIELDS = [
    "node_id",
    "node_type",
    "entity_id",
    "entity_name",
    "content",
    "properties",
]


class AzureDirectSearchClient:
    """
    Thin wrapper around the Azure AI Search SDK that handles:

    * Service-principal authentication via `ClientSecretCredential`
    * Local query vectorisation via :class:`~src.clients.embedding.EmbeddingClient`
    * Vector, BM25, and hybrid (RRF) search modes

    Parameters
    ----------
    endpoint:       Azure AI Search service endpoint URL.
    index_name:     Target index name.
    tenant_id:      Azure AD tenant ID for the service principal.
    client_id:      Service-principal application (client) ID.
    client_secret:  Service-principal secret.
    credential:     Optional pre-built `TokenCredential` (overrides the
                    service-principal fields above when provided).
    embedding_client: Optional pre-built :class:`EmbeddingClient`.
    """

    def __init__(
        self,
        endpoint: str = config.AZURE_SEARCH_ENDPOINT,
        index_name: str = config.AZURE_SEARCH_INDEX_NAME,
        tenant_id: str = config.AZURE_TENANT_ID,
        client_id: str = config.AZURE_CLIENT_ID,
        client_secret: str = config.AZURE_CLIENT_SECRET,
        credential: Optional[TokenCredential] = None,
        embedding_client: Optional[EmbeddingClient] = None,
    ) -> None:
        self._index_name = index_name

        self._credential: TokenCredential = credential or ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )

        self._search_client = SearchClient(
            endpoint=endpoint,
            index_name=index_name,
            credential=self._credential,
        )

        self._embedder = embedding_client or EmbeddingClient()

    # -------------------------------------------------------------------------
    # Public search methods
    # -------------------------------------------------------------------------

    def search_vector(
        self,
        query: str,
        top_k: int = 5,
        vector_field: str = "content_vector",
        exhaustive: bool = False,
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Pure vector (HNSW / exhaustive kNN) search.

        The query is embedded locally and sent as a pre-computed
        `VectorizedQuery` so no server-side vectorisation is required.

        Parameters
        ----------
        query:              Natural-language query string.
        top_k:              Maximum number of results to return.
        vector_field:       Name of the index field that holds document vectors.
        exhaustive:         When *True*, uses exhaustive kNN instead of HNSW ANN.
        filter_expression:  OData filter string applied before the vector search.
        select:             Fields to include in each result document.

        Returns
        -------
        List of result dicts, each enriched with `@search.score`.
        """
        vector = self._embedder.embed(query)

        vector_query = VectorizedQuery(
            vector=vector,
            k_nearest_neighbors=top_k,
            fields=vector_field,
            exhaustive=exhaustive,
        )

        results = self._search_client.search(
            search_text=None,           # vector-only: no BM25 keyword pass
            vector_queries=[vector_query],
            filter=filter_expression,
            top=top_k,
            select=select or _DEFAULT_SELECT_FIELDS,
        )

        return self._collect(results)

    def search_bm25(
        self,
        query: str,
        top_k: int = 5,
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
        search_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Pure keyword (BM25) search - no vector involved.

        Parameters
        ----------
        query:              Natural-language query string.
        top_k:              Maximum number of results to return.
        filter_expression:  OData filter string.
        select:             Fields to include in each result document.
        """
        results = self._search_client.search(
            search_text=query,
            filter=filter_expression,
            top=top_k,
            select=select or _DEFAULT_SELECT_FIELDS,
            search_fields=search_fields,
        )

        return self._collect(results)

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        vector_field: str = "content_vector",
        exhaustive: bool = False,
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
        search_fields: Optional[List[str]] = None,
        semantic_config: Optional[str] = config.AZURE_SEARCH_SEMANTIC_CONFIG,
        use_semantic_rerank: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search: BM25 keyword + vector (RRF fusion), with optional
        semantic re-ranking.

        Parameters
        ----------
        query:              Natural-language query string.
        top_k:              Maximum number of results to return.
        vector_field:       Name of the index field that holds document vectors.
        exhaustive:         When *True*, uses exhaustive kNN for the vector leg.
        filter_expression:  OData filter string.
        select:             Fields to include in each result document.
        semantic_config:    Name of the semantic configuration in the index.
        use_semantic_rerank: When *True*, applies semantic re-ranking on top of
                             the RRF-fused results (requires semantic config).
        """
        vector = self._embedder.embed(query)

        vector_query = VectorizedQuery(
            vector=vector,
            k_nearest_neighbors=top_k,
            fields=vector_field,
            exhaustive=exhaustive,
        )

        search_kwargs: Dict[str, Any] = dict(
            search_text=query,
            vector_queries=[vector_query],
            filter=filter_expression,
            top=top_k,
            select=select or _DEFAULT_SELECT_FIELDS,
            search_fields=search_fields,
        )

        if use_semantic_rerank and semantic_config:
            search_kwargs["query_type"] = "semantic"
            search_kwargs["semantic_configuration_name"] = semantic_config
            search_kwargs["query_caption"] = "extractive"
            search_kwargs["query_answer"] = "extractive"

        results = self._search_client.search(**search_kwargs)

        return self._collect(results)

    # -------------------------------------------------------------------------
    # LangChain-compatible interface
    # -------------------------------------------------------------------------

    def get_relevant_documents(
        self,
        query: str,
        top_k: int = 5,
    ) -> List[Any]:
        """Return LangChain `Document` objects using hybrid search."""
        from langchain_core.documents import Document

        raw = self.search_hybrid(query, top_k=top_k)
        return [
            Document(
                page_content=doc.get("content", "") or "",
                metadata=doc,
            )
            for doc in raw
        ]

    def upload_documents(self, documents: List[Dict[str, Any]]) -> Any:
        """Upload documents to the configured Azure AI Search index."""
        return self._search_client.upload_documents(documents=documents)

    def merge_or_upload_documents(self, documents: List[Dict[str, Any]]) -> Any:
        """Merge existing documents or upload them when they do not exist."""
        return self._search_client.merge_or_upload_documents(documents=documents)

    def delete_documents(self, documents: List[Dict[str, Any]]) -> Any:
        """Delete documents from the configured Azure AI Search index."""
        return self._search_client.delete_documents(documents=documents)

    def search_all(
        self,
        search_text: str = "*",
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Return all matching documents for administrative operations."""
        results = self._search_client.search(
            search_text=search_text,
            filter=filter_expression,
            select=select or _DEFAULT_SELECT_FIELDS,
        )
        return self._collect(results)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _collect(result_iterator: Any) -> List[Dict[str, Any]]:
        """Materialise the lazy SDK result iterator into a plain list."""
        docs: List[Dict[str, Any]] = []
        for result in result_iterator:
            doc = dict(result)
            # Preserve score values from SDK payload first; only fallback to result.get.
            score = doc.get("@search.score")
            if score is None:
                try:
                    score = result.get("@search.score", 0.0)
                except Exception:
                    score = 0.0
            doc["@search.score"] = score if score is not None else 0.0

            # Capture semantic reranker score when available.
            reranker = doc.get("@search.reranker_score")
            if reranker is None:
                try:
                    reranker = result.get("@search.reranker_score")
                except Exception:
                    reranker = None
            if reranker is not None:
                doc["@search.reranker_score"] = reranker

            docs.append(doc)

        return docs
