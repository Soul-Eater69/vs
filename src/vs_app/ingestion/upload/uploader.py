"""Dry-run-safe, JSONL-first uploader for Theme-generation POC documents.

Consumes documents produced by ``build_theme_generation_documents`` (one JSON
doc per line). Defaults to dry-run: it reads/summarises documents and makes zero
Azure and zero embedding calls. Real uploads happen only when ``dry_run=False``;
embeddings only when explicitly requested, and only for IDMT documents. Theme
documents are never embedded and never require ``content_vector``.

No Jira access, no runtime theme generation here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vs_app.ingestion.upload.azure_search_client import (
    embedding_model,
    make_documents_client,
    make_embedding_client,
    resolve_index_name,
)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            docs.append(json.loads(stripped))
    return docs


def write_jsonl(path: str | Path, docs: list[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs)
    Path(path).write_text(lines, encoding="utf-8")


def summarize_documents(docs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(docs),
        "idmt": sum(1 for doc in docs if doc.get("document_type") == "idmt"),
        "theme": sum(1 for doc in docs if doc.get("document_type") == "theme"),
        "with_vectors": sum(1 for doc in docs if doc.get("content_vector")),
    }


def embed_idmt_documents(
    docs: list[dict[str, Any]],
    *,
    embedding_client: Any | None = None,
) -> list[dict[str, Any]]:
    """Populate ``content_vector`` for IDMT docs that lack one. Theme docs untouched."""
    from vs_app.integrations.embeddings.client import embed_batch

    targets = [
        doc
        for doc in docs
        if doc.get("document_type") == "idmt" and not doc.get("content_vector")
    ]
    if not targets:
        return docs

    client = embedding_client or make_embedding_client()
    vectors = embed_batch(
        [str(doc.get("content") or "") for doc in targets],
        client,
        model=embedding_model(),
    )
    for doc, vector in zip(targets, vectors):
        doc["content_vector"] = [float(value) for value in vector]
    return docs


def upload_theme_generation_documents(
    *,
    docs: list[dict[str, Any]],
    index_name: str | None = None,
    dry_run: bool = True,
    documents_client: Any | None = None,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Upload docs to the POC index. Dry-run (default) makes zero Azure calls."""
    name = resolve_index_name(index_name)
    summary = summarize_documents(docs)
    result: dict[str, Any] = {
        "index_name": name,
        "dry_run": dry_run,
        "uploaded": False,
        **summary,
    }
    if dry_run:
        return result

    client = documents_client or make_documents_client()
    result["gateway_response"] = client.upload_documents(
        index_name=name,
        documents=docs,
        batch_size=batch_size,
    )
    result["uploaded"] = True
    return result


__all__ = [
    "read_jsonl",
    "write_jsonl",
    "summarize_documents",
    "embed_idmt_documents",
    "upload_theme_generation_documents",
]
