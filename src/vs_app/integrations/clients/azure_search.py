"""
Azure AI Search - unified client.

Handles both document upload (direct Push API via service-principal) and
search queries (via the IDP gateway which handles vectorization server-side).

Usage
-----
from vs_app.integrations.clients.azure_search import AzureSearchClient, IDPAISearchRequest

client = AzureSearchClient()

# Upload
client.upload_documents(documents)

# Search via IDP gateway
docs = client.search("provider contracting", top=5)

# LangChain retriever interface
lc_docs = client.get_relevant_documents("provider contracting")
"""

from __future__ import annotations

import logging
from dataclasses import asdict, field, replace
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from azure.identity import ClientSecretCredential
from langchain_core.documents import Document
from pydantic.dataclasses import dataclass as pydantic_dataclass

from vs_app import settings as config
from .auth import IDPCustomAuth

logger = logging.getLogger(__name__)

_SEARCH_API_VERSION = "2024-05-01-preview"
_SEARCH_API_PATH = "/api/v1/aisearch/search"

# -------------------------------------------------------------------------
# Request / response dataclasses (previously in idp_search.py)
# -------------------------------------------------------------------------


@pydantic_dataclass
class IDPAISearchRequest:
    """Maps 1:1 to the backend API schema."""

    index_name: str = "your-index-name"
    search_text: str = "*"
    search_type: str = "simple"                     # "simple" | "semantic"
    hybrid_mode: Optional[str] = None               # "rrf" | None
    vector_weight: float = 0.5
    text_weight: float = 0.5
    filter_expression: Optional[str] = None
    pre_filters: Optional[Dict] = None
    post_filters: Optional[Dict] = None
    vector_filter_mode: str = "preFilter"
    vector_queries: Optional[List[Dict]] = None     # raw vectors if pre-computed
    enable_vector_search: bool = False
    vector_fields: Optional[List[str]] = field(default_factory=lambda: ["content_vector"])
    embedding_params: Optional[Dict[str, Any]] = field(
        default_factory=lambda: {
            "api_version": "2024-06-01",
            "encoding_format": "float",
            "dimensions": 0,                        # filled from runtime settings
            "model": "string",                      # filled from runtime settings
        }
    )
    k_nearest_neighbors: int = 50
    top: int = 5
    skip: int = 0
    order_by: Optional[List[str]] = None
    select: Optional[List[str]] = None
    semantic_configuration_name: Optional[str] = "default"  # override from runtime settings when needed
    query_caption: Optional[str] = None
    query_answer: Optional[str] = None
    query_answer_count: int = 5
    scoring_profile: Optional[str] = None
    scoring_parameters: Optional[List[str]] = None
    facets: Optional[List[str]] = None
    highlight_fields: Optional[List[str]] = None
    highlight_pre_tag: str = "<em>"
    highlight_post_tag: str = "</em>"
    include_total_count: bool = True
    minimum_coverage: int = 0
    session_id: Optional[str] = None


@pydantic_dataclass
class IDPAISearchResponse:
    documents: List[Dict[str, Any]]
    total_count: int
    facets: Dict[str, Any] = field(default_factory=dict)
    semantic_answers: Optional[List] = None
    search_metadata: Dict = field(default_factory=dict)
    performance_metrics: Dict = field(default_factory=dict)
    applied_filters: Dict = field(default_factory=dict)


def _remove_nulls(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


class AzureSearchClient:

    def __init__(
        self,
        endpoint: str = config.AZURE_SEARCH_ENDPOINT,
        index_name: str = config.AZURE_SEARCH_INDEX_NAME,
        tenant_id: str = config.AZURE_TENANT_ID,
        client_id: str = config.AZURE_CLIENT_ID,
        client_secret: str = config.AZURE_CLIENT_SECRET,
        idp_app_id: str = config.AISEARCH_APP_ID,
        idp_base_url: str = config.AISEARCH_BASE_URL,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._index_name = index_name
        self._idp_base_url = idp_base_url
        # service-principal credentials for direct Azure API calls
        self._credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        # IDP gateway auth for search calls
        self._idp_auth = IDPCustomAuth(app_id=idp_app_id)

    def _azure_headers(self) -> Dict[str, str]:
        token = self._credential.get_token("https://search.azure.com/.default").token
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def get_relevant_documents(
        self,
        query: str,
        request: Optional[IDPAISearchRequest] = None,
    ) -> List[Document]:
        """LangChain-compatible interface. Returns `Document` objects."""
        response = self.search(query, request=request)
        return [
            Document(page_content=doc.get("content", "") or "", metadata=doc)
            for doc in response.documents
        ]

    def search(self, query: str, request: Optional[IDPAISearchRequest] = None) -> Any:
        if not self._idp_base_url:
            raise ValueError("AISEARCH_BASE_URL is not set.")

        if request is None:
            request = IDPAISearchRequest()

        # Only override search_text if it's still the default wildcard.
        # This lets callers explicitly set search_text="*" for vector-only.
        if request.search_text == "*" and request.enable_vector_search:
            # Vector-only: keep "*" for BM25, backend uses query for embedding
            # The query is passed separately or embedded via embedding_params
            payload = request
        else:
            payload = replace(request, search_text=query)

        # ... rest of HTTP call
        url = urljoin(self._idp_base_url, _SEARCH_API_PATH)

        payload_dict = asdict(payload)

        # Always remove vector_fields when vector_queries is present - they're mutually
        # exclusive ways to drive vector search and sending both confuses the API.
        # Also unconditionally delete it to guard against pydantic default_factory.
        payload_dict.pop("vector_fields", None)

        clean_payload = _remove_nulls(payload_dict)
        import json as _json
        # print("OUTBOUND PAYLOAD:", _json.dumps({k: v for k, v in clean_payload.items() if k != "vector_queries"}, indent=2))
        if "vector_queries" in clean_payload:
            print(">>> vector_queries:", _json.dumps([{k: ("<vector>" if k == "vector" else v) for k, v in vq.items()} for vq in clean_payload["vector_queries"]], indent=2))

        with httpx.Client(verify=False) as client:
            resp = client.post(
                url,
                auth=self._idp_auth,
                json=clean_payload,
                timeout=30,
            )

        return IDPAISearchResponse(**resp.json())

    def search_bm25(
        self,
        query: str,
        top_k: int = 5,
        filter_expression: Optional[str] = None,
    ) -> Any:
        request = IDPAISearchRequest(
            search_type="simple",
            search_text=query,          # drives BM25 matching
            top=top_k,
            filter_expression=filter_expression,
            enable_vector_search=False, # no vector search
            hybrid_mode=None,           # no fusion
            text_weight=1.0,            # irrelevant without hybrid, but explicit
            vector_weight=0.0,
        )
        return self.search(query, request=request)

    def search_vector(
        self,
        query: str,
        top_k: int = 5,
        filter_expression: Optional[str] = None,
    ) -> Any:
        if not self._idp_base_url:
            raise ValueError("AISEARCH_BASE_URL is not set.")

        # Build raw payload - server-side embedding path (no vector_queries)
        payload: Dict[str, Any] = {
            "index_name": "idp_kg_data_test",
            "search_type": "simple",
            "top": top_k,
            "vector_queries": [{
                "fields": "content_vector",
                "vector": query,
                "k": 50,
                "exhaustive": False
            }],
            "embedding_params": {
                "api_version": "2024-05-01",
                "encoding_format": "float",
                "dimensions": config.EMBEDDING_DIMENSION,
                "model": config.EMBEDDING_MODEL,
            },
            "select": ["node_id", "node_type", "entity_id", "entity_name", "content", "properties"],
            "include_total_count": True,
        }

        print(payload)
        if filter_expression:
            payload["filter_expression"] = filter_expression

        url = urljoin(self._idp_base_url, _SEARCH_API_PATH)
        with httpx.Client(verify=False) as client:
            resp = client.post(url, auth=self._idp_auth, json=payload, timeout=30)

        if not resp.is_success:
            raise RuntimeError(f"IDP Search {resp.status_code}: {resp.text}")

        return IDPAISearchResponse(**resp.json())

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        filter_expression: Optional[str] = None,
        text_weight: float = 0.5,
        vector_weight: float = 0.5,
    ) -> Any:
        request = IDPAISearchRequest(
            search_type="semantic",
            search_text=query,
            top=top_k,
            filter_expression=filter_expression,
            enable_vector_search=True,
            hybrid_mode="rrf",
            text_weight=text_weight,
            vector_weight=vector_weight,
            semantic_configuration_name="default",
            query_caption="extractive|highlight-true",
            query_answer="extractive",
            query_answer_count=3,
            k_nearest_neighbors=50,       # feed semantic ranker plenty
        )
        return self.search(query, request=request)
