"""Retrieval helpers for runtime Theme generation (Feature 14A).

Pure orchestration over an injected search client. These helpers do NOT embed
text, construct an Azure client, call an LLM, or fetch Jira; they take a
precomputed ``query_vector`` and a ``search_client`` so they are fully testable
with fakes. The production adapter over ``AzureDirectSearchClient`` is wired in
Feature 14B.

Injected ``search_client`` contract:
- ``vector_search(*, query_vector, top_k, filter_expression, select=None,
  index_name=None) -> list[dict]`` — vector search returning result dicts
  (Azure-style, may carry ``@search.score``).
- ``get_document(*, doc_id, index_name=None) -> dict | None`` — fetch a single
  document by key, or ``None`` if absent. (A client exposing ``get_documents``
  for batch lookup is also supported.)

Design rules (see the Feature 14 audit):
- Vector search IDMT docs only (``document_type eq 'idmt'``).
- Theme docs are never vector-searched; they are fetched deterministically by id.
- Historic examples inform writing style, never allowed labels.
"""

from __future__ import annotations

from typing import Any

IDMT_FILTER = "document_type eq 'idmt'"

# Compact fields kept for prompt examples (no vectors, no raw blobs).
_EXAMPLE_PROPERTY_FIELDS = (
    "theme_description",
    "business_needs",
    "value_streams",
    "stages",
)


def search_idmt_examples(
    *,
    search_client: Any,
    query_vector: list[float],
    top_k: int = 15,
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Vector-search the POC index for similar IDMT documents only.

    Always applies the ``document_type eq 'idmt'`` filter and, defensively,
    drops any non-IDMT doc the client returns. Does not embed; uses the supplied
    ``query_vector`` and injected ``search_client``.
    """
    results = search_client.vector_search(
        query_vector=query_vector,
        top_k=top_k,
        filter_expression=IDMT_FILTER,
        index_name=index_name,
    )
    return [
        doc
        for doc in (results or [])
        if isinstance(doc, dict) and _doc_type(doc) == "idmt"
    ]


def extract_matching_theme_refs(
    *,
    idmt_docs: list[dict[str, Any]],
    value_stream_name: str,
    max_refs: int = 5,
) -> list[dict[str, Any]]:
    """Pull (ticket_id, group_id) refs for the selected Value Stream.

    Inspects each IDMT doc's ``properties.value_streams``, matches the selected
    value stream case/space-insensitively, dedupes by (ticket_id, group_id)
    preferring ``direct`` over ``implied`` on duplicates, preserves the original
    ranking order otherwise, and caps to ``max_refs``.
    """
    target = _norm(value_stream_name)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for doc in idmt_docs or []:
        if not isinstance(doc, dict):
            continue
        ticket_id = _text(doc.get("ticket_id"))
        score = _score(doc)
        properties = doc.get("properties") or {}
        for vs in properties.get("value_streams") or []:
            if not isinstance(vs, dict):
                continue
            if _norm(vs.get("value_stream_name")) != target:
                continue
            group_id = _text(vs.get("group_id"))
            if not ticket_id or not group_id:
                continue
            ref = {
                "ticket_id": ticket_id,
                "group_id": group_id,
                "value_stream_name": _text(vs.get("value_stream_name")),
                "support_type": _text(vs.get("support_type")),
                "reason": _text(vs.get("reason")),
                "evidence": _text(vs.get("evidence")),
                "score": score,
            }
            key = (ticket_id, group_id)
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = ref
                order.append(key)
            elif _prefer(ref, existing):
                by_key[key] = ref

    refs = [by_key[key] for key in order]
    return refs[:max_refs] if max_refs is not None else refs


def fetch_theme_examples(
    *,
    search_client: Any,
    refs: list[dict[str, Any]],
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch theme docs deterministically by id for each ref, preserving order.

    Theme docs are never vector-searched. Missing docs are skipped, not failed.
    """
    docs: list[dict[str, Any]] = []
    for ref in refs or []:
        ticket_id = _text(ref.get("ticket_id"))
        group_id = _text(ref.get("group_id"))
        if not ticket_id or not group_id:
            continue
        doc_id = f"theme::{ticket_id}::{group_id}"
        doc = _get_document(search_client, doc_id, index_name)
        if isinstance(doc, dict) and doc:
            docs.append(doc)
    return docs


def select_theme_examples_for_prompt(
    *,
    theme_docs: list[dict[str, Any]],
    max_examples: int = 5,
) -> list[dict[str, Any]]:
    """Produce compact prompt examples; drop vectors, raw blobs, and empties."""
    examples: list[dict[str, Any]] = []
    for doc in theme_docs or []:
        if not isinstance(doc, dict):
            continue
        properties = doc.get("properties") or {}
        theme_description = _text(properties.get("theme_description"))
        business_needs = _text(properties.get("business_needs"))
        if not theme_description and not business_needs:
            continue  # nothing to learn from
        examples.append(
            {
                "ticket_id": _text(doc.get("ticket_id")),
                "group_id": _text(doc.get("group_id")),
                "theme_description": theme_description,
                "business_needs": business_needs,
                "value_streams": list(properties.get("value_streams") or []),
                "stages": list(properties.get("stages") or []),
            }
        )
        if max_examples is not None and len(examples) >= max_examples:
            break
    return examples


def _get_document(search_client: Any, doc_id: str, index_name: str | None) -> Any:
    get_document = getattr(search_client, "get_document", None)
    if callable(get_document):
        return get_document(doc_id=doc_id, index_name=index_name)
    get_documents = getattr(search_client, "get_documents", None)
    if callable(get_documents):
        results = get_documents(doc_ids=[doc_id], index_name=index_name) or []
        return results[0] if results else None
    raise TypeError("search_client must provide get_document() or get_documents()")


def _prefer(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    """Prefer a direct-support ref over a non-direct duplicate."""
    return _norm(candidate.get("support_type")) == "direct" and _norm(
        existing.get("support_type")
    ) != "direct"


def _doc_type(doc: dict[str, Any]) -> str:
    return _norm(doc.get("document_type"))


def _score(doc: dict[str, Any]) -> float:
    for key in ("@search.reranker_score", "@search.score", "score"):
        value = doc.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return _text(value).lower()


__all__ = [
    "IDMT_FILTER",
    "search_idmt_examples",
    "extract_matching_theme_refs",
    "fetch_theme_examples",
    "select_theme_examples_for_prompt",
]
