from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from vs_app.integrations.clients.embedding import EmbeddingClient
from vs_app.modules.rag.query.retrieval_summary import format_structured_summary_text

logger = logging.getLogger(__name__)

DEFAULT_FAISS_DIR = Path("local_faiss")
REPO_ROOT = Path(__file__).resolve().parents[1]


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

    summary_docs, indexed_summary_ids, skipped_summaries = _build_summary_documents(summaries)
    chunk_docs = [_chunk_to_document(row) for row in chunks if str(row.get("text") or "").strip()]

    summary_count = _build_index(summary_docs, index_path / "summaries", embeddings)
    chunk_count = _build_index(chunk_docs, index_path / "chunks", embeddings)

    _write_json(index_path / "summary_docs.json", summaries)
    _write_json(index_path / "skipped_summary_docs.json", skipped_summaries)
    _write_json(index_path / "chunk_docs.json", chunks)
    _write_json(
        index_path / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_output_dir": str(output_path),
            "source_summary_count": len(summaries),
            "summary_doc_count": summary_count,
            "summary_doc_ids": indexed_summary_ids,
            "skipped_summary_count": len(skipped_summaries),
            "skipped_summary_docs": skipped_summaries,
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
        "source_summary_count": len(summaries),
        "summary_doc_count": summary_count,
        "skipped_summary_count": len(skipped_summaries),
        "skipped_summary_docs": skipped_summaries,
        "chunk_doc_count": chunk_count,
    }


def faiss_index_exists(
    *,
    index_dir: str | Path = DEFAULT_FAISS_DIR,
    kind: str = "summaries",
) -> bool:
    return _resolve_existing_index_path(index_dir=index_dir, kind=kind) is not None


def search_local_faiss(
    query_text: str,
    *,
    index_dir: str | Path = DEFAULT_FAISS_DIR,
    kind: str = "summaries",
    top_k: int = 8,
    embedding: EmbeddingClient | None = None,
) -> list[dict]:
    index_path = _resolve_existing_index_path(index_dir=index_dir, kind=kind)
    if index_path is None:
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


def _resolve_existing_index_path(
    *,
    index_dir: str | Path,
    kind: str,
) -> Path | None:
    raw = Path(index_dir)
    candidates: list[Path] = []

    # Normal case: caller passes the FAISS root, we append kind.
    candidates.append(raw / kind)

    # Also allow callers to pass the concrete summaries/chunks directory directly.
    if raw.name.lower() == kind.lower():
        candidates.append(raw)

    # Be resilient to relative-path confusion by resolving against the repo root too.
    if not raw.is_absolute():
        repo_relative = REPO_ROOT / raw
        candidates.append(repo_relative / kind)
        if repo_relative.name.lower() == kind.lower():
            candidates.append(repo_relative)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if (candidate / "index.faiss").exists() and (candidate / "index.pkl").exists():
            return candidate

    return None


def _load_summary_artifacts(output_dir: Path) -> list[dict]:
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
    return format_structured_summary_text(summary)


def _build_summary_documents(summaries: list[dict]) -> tuple[list[Document], list[str], list[dict]]:
    docs: list[Document] = []
    indexed_ids: list[str] = []
    skipped: list[dict] = []

    for index, row in enumerate(summaries):
        ticket_id = str(row.get("ticket_id") or row.get("key") or "").strip()
        text = _summary_text(row).strip()
        if not text:
            skipped.append(
                {
                    "ticket_id": ticket_id,
                    "index": index,
                    "reason": "empty_formatted_summary_text",
                    "present_fields": sorted(
                        key for key, value in row.items() if value not in ("", [], {}, None)
                    ),
                }
            )
            continue

        docs.append(_summary_to_document(row))
        if ticket_id:
            indexed_ids.append(ticket_id)

    return docs, indexed_ids, skipped


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
