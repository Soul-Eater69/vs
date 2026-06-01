"""Client/config accessors for the Theme-generation POC Azure index.

Reuses the existing IDP documents client, the Azure SDK index client, and the
embedding client. Adds a dedicated index-name env var so this POC can never
touch the value-stream, historical, or summary indexes. Real clients are
constructed lazily, so dry-run paths and tests make no Azure calls.
"""

from __future__ import annotations

import os
from typing import Any

from vs_app import settings

DEFAULT_THEME_GENERATION_INDEX_NAME = "idp_theme_generation_poc"
THEME_GENERATION_INDEX_NAME_ENV = "THEME_GENERATION_AZURE_SEARCH_INDEX_NAME"

# Index names this POC must never create/recreate/delete.
PROTECTED_INDEX_NAMES = frozenset(
    name
    for name in (
        settings.AZURE_SEARCH_INDEX_NAME,
        settings.VALUE_STREAM_AZURE_SEARCH_INDEX_NAME,
        settings.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    )
    if name
)


def resolve_index_name(override: str | None = None) -> str:
    """Resolve the theme-generation index name (override > env > default)."""
    if override and override.strip():
        return override.strip()
    env_value = os.getenv(THEME_GENERATION_INDEX_NAME_ENV, "").strip()
    return env_value or DEFAULT_THEME_GENERATION_INDEX_NAME


def embedding_dimensions() -> int:
    """Vector dimensions for the deployed embedding model (never hard-coded)."""
    return int(getattr(settings, "EMBEDDING_DIMENSION", 1536) or 1536)


def embedding_model() -> str:
    return getattr(settings, "EMBEDDING_MODEL", "") or "text-embedding-3-large"


def make_documents_client() -> Any:
    from vs_app.integrations.clients.aisearch_documents import AISearchDocumentsClient

    return AISearchDocumentsClient()


def make_index_client() -> Any:
    from azure.identity import ClientSecretCredential
    from azure.search.documents.indexes import SearchIndexClient

    credential = ClientSecretCredential(
        tenant_id=getattr(settings, "AZURE_TENANT_ID", ""),
        client_id=getattr(settings, "AZURE_CLIENT_ID", ""),
        client_secret=getattr(settings, "AZURE_CLIENT_SECRET", ""),
    )
    return SearchIndexClient(endpoint=settings.AZURE_SEARCH_ENDPOINT, credential=credential)


def make_embedding_client() -> Any:
    from vs_app.integrations.clients.embedding import EmbeddingClient

    return EmbeddingClient()


__all__ = [
    "DEFAULT_THEME_GENERATION_INDEX_NAME",
    "THEME_GENERATION_INDEX_NAME_ENV",
    "PROTECTED_INDEX_NAMES",
    "resolve_index_name",
    "embedding_dimensions",
    "embedding_model",
    "make_documents_client",
    "make_index_client",
    "make_embedding_client",
]
