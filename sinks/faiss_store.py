from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from ..clients.embedding import EmbeddingClient

logger = logging.getLogger(__name__)

DEFAULT_FAISS_DIR = Path("local_faiss")


def build_local_faiss_indexes(
    *,
    output_dir: str | Path = "ticket_data",
    index_dir: str | Path = DEFAULT_FAISS_DIR,
    embedding: EmbeddingClient | None = None,
) -> dict:
    output_path = Path(output_dir)
    index_path = Path(index_dir)
    summaries = _load_summary_artifacts(output_path)
    chunks = _load_chunk_artifacts(output_path)

    index_path.mkdir(parents=True, exist_ok=True)
    embeddings = embedding or EmbeddingClient()

    summary_docs = [_summary_to_document(row) for row in summaries if _summary_text(row)]
    chunk_docs = [_chunk_to_document(row) for row in chunks if str(row.get("text") or "").strip()]

    summary_count = _build_index(summary_docs, index_path / "summaries", embeddings)
    chunk_count = _build_index(chunk_docs, index_path / "chunks", embeddings)

    _write_json(index_path / "summary_docs.json", summaries)
    _write_json(index_path / "chunk_docs.json", chunks)
    _write_json(
        index_path / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_output_dir": str(output_path),
            "summary_doc_count": summary_count,
            "chunk_doc_count": chunk_count,
            "summary_index_dir": str(index_path / "summaries"),
            "chunk_index_dir": str(index_path / "chunks"),
        },
    )

    logger.info(
        "[FAISS] Built local indexes at %s (%d summaries, %d chunks)",
        index_path,
        summary_count,
        chunk_count,
    )
    return {
        "index_dir": str(index_path),
        "summary_doc_count": summary_count,
        "chunk_doc_count": chunk_count,
    }


def faiss_index_exists(
    *,
    index_dir: str | Path = DEFAULT_FAISS_DIR,
    kind: str = "summaries",
) -> bool:
    index_path = Path(index_dir) / kind
    return (index_path / "index.faiss").exists() and (index_path / "index.pkl").exists()


def search_local_faiss(
    query_text: str,
    *,
    index_dir: str | Path = DEFAULT_FAISS_DIR,
    kind: str = "summaries",
    top_k: int = 8,
    embedding: EmbeddingClient | None = None,
) -> list[dict]:
    index_path = Path(index_dir) / kind
    if not faiss_index_exists(index_dir=index_dir, kind=kind):
        return []

    from langchain_community.vectorstores import FAISS

    embeddings = embedding or EmbeddingClient()
    vectorstore = FAISS.load_local(
        str(index_path),
        embeddings,
        allow_dangerous_deserialization=True,
    )
    results = vectorstore.similarity_search_with_score(query_text, k=top_k)

    out: list[dict] = []
    for rank, (doc, raw_distance) in enumerate(results, start=1):
        similarity = round(1.0 / (1.0 + float(raw_distance)), 4)
        out.append(
            {
                "rank": rank,
                "score": similarity,
                "content": doc.page_content,
                "metadata": dict(doc.metadata or {}),
            }
        )
    return out


def _load_summary_artifacts(output_dir: Path) -> list[dict]:
    aggregate_path = output_dir / "_all_summaries.json"
    if aggregate_path.exists():
        payload = _read_json(aggregate_path)
        if isinstance(payload, dict):
            return [row for row in (payload.get("summaries") or []) if isinstance(row, dict)]

    docs: list[dict] = []
    for path in sorted(output_dir.glob("*/summary.json")):
        payload = _read_json(path)
        if isinstance(payload, dict):
            docs.append(payload)
    return docs


def _load_chunk_artifacts(output_dir: Path) -> list[dict]:
    aggregate_path = output_dir / "_all_chunks.json"
    if aggregate_path.exists():
        payload = _read_json(aggregate_path)
        if isinstance(payload, dict):
            return [row for row in (payload.get("chunks") or []) if isinstance(row, dict)]

    docs: list[dict] = []
    for path in sorted(output_dir.glob("*/chunks.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict):
            continue
        docs.extend([row for row in (payload.get("documents") or []) if isinstance(row, dict)])
        docs.extend([row for row in (payload.get("sections") or []) if isinstance(row, dict)])
    return docs


def _summary_to_document(summary: dict) -> Document:
    ticket_id = str(summary.get("ticket_id") or "").strip()
    metadata = {
        "doc_type": "summary",
        "ticket_id": ticket_id,
        "value_stream_names": summary.get("value_stream_names", []),
        "value_stream_ids": summary.get("value_stream_ids", []),
        "direct_vs_names": summary.get("direct_vs_names", []),
        "implied_vs_names": summary.get("implied_vs_names", []),
        "label_source": summary.get("label_source", ""),
    }
    return Document(page_content=_summary_text(summary), metadata=metadata)


def _summary_text(summary: dict) -> str:
    parts = [str(summary.get("summary_text") or "").strip()]
    business_problem = str(summary.get("business_problem") or "").strip()
    business_capability = str(summary.get("business_capability") or "").strip()
    if business_problem:
        parts.append(f"Problem: {business_problem}")
    if business_capability:
        parts.append(f"Capability: {business_capability}")
    key_terms = [str(term).strip() for term in (summary.get("key_terms") or []) if str(term).strip()]
    if key_terms:
        parts.append("Key Terms: " + ", ".join(key_terms))

    value_streams = summary.get("value_streams") or []
    if isinstance(value_streams, list) and value_streams:
        lines = []
        for row in value_streams:
            if not isinstance(row, dict):
                continue
            name = str(row.get("vs_name") or "").strip()
            inference_type = str(row.get("inference_type") or "").strip()
            reason = str(row.get("reason") or "").strip()
            if not name:
                continue
            line = name
            if inference_type:
                line += f" [{inference_type}]"
            if reason:
                line += f": {reason}"
            lines.append(line)
        if lines:
            parts.append("Value Streams:\n" + "\n".join(lines[:8]))

    return "\n".join(part for part in parts if part and part.strip()).strip()


def _chunk_to_document(chunk: dict) -> Document:
    metadata = {
        "doc_type": "chunk",
        "ticket_id": str(chunk.get("ticket_id") or "").strip(),
        "chunk_uid": str(chunk.get("chunk_uid") or "").strip(),
        "level": str(chunk.get("level") or "").strip(),
        "source": str(chunk.get("source") or "").strip(),
        "section_title": str(chunk.get("section_title") or "").strip(),
        "attachment_id": str(chunk.get("attachment_id") or "").strip(),
        "attachment_name": str(chunk.get("attachment_name") or "").strip(),
        "value_stream_names": chunk.get("value_stream_names", []),
        "value_stream_ids": chunk.get("value_stream_ids", []),
    }
    return Document(page_content=str(chunk.get("text") or "").strip(), metadata=metadata)


def _build_index(documents: list[Document], index_dir: Path, embeddings: EmbeddingClient) -> int:
    if not documents:
        return 0

    from langchain_community.vectorstores import FAISS

    index_dir.mkdir(parents=True, exist_ok=True)
    vectorstore = FAISS.from_documents(documents, embeddings)
    vectorstore.save_local(str(index_dir))
    return len(documents)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
