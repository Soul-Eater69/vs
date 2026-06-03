"""Read-only search adapter satisfying the orchestrator's client contract.

Bridges ``AzureDirectSearchClient`` (which exposes ``search_by_vector`` /
``get_document_by_id``) to the contract the Theme-generation orchestrator and
retrieval helpers expect:

    vector_search(*, query_vector, top_k, filter_expression, select=None, index_name=None)
    get_document(*, doc_id, index_name=None)

Read-only: it only searches and fetches by id. It never embeds, never calls an
LLM or Jira, and never uploads/creates/deletes. Embedding (producing
``query_vector``) and client construction happen in the caller / Feature 15B CLI.
"""

from __future__ import annotations

from typing import Any


class ThemeGenerationSearchAdapter:
    def __init__(self, *, client: Any, index_name: str | None = None) -> None:
        self._client = client
        self._index_name = index_name

    def vector_search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filter_expression: str,
        select: list[str] | None = None,
        index_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Delegate to the underlying client's precomputed-vector search."""
        results = self._client.search_by_vector(
            vector=query_vector,
            top_k=top_k,
            filter_expression=filter_expression,
            select=select,
            index_name=self._resolve_index(index_name),
        )
        return list(results or [])

    def get_document(
        self,
        *,
        doc_id: str,
        index_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a single document by id; ``None`` if missing."""
        doc = self._client.get_document_by_id(
            doc_id=doc_id,
            index_name=self._resolve_index(index_name),
        )
        return dict(doc) if isinstance(doc, dict) else None

    def _resolve_index(self, index_name: str | None) -> str | None:
        # Per-call override wins; else the adapter's configured index; else the
        # underlying client's default applies (None).
        return index_name or self._index_name


__all__ = ["ThemeGenerationSearchAdapter"]
