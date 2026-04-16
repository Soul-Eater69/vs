from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from jira import JiraValueStreamClient
from pipelines.jira_batch.runtime.runtime_factory import (
    build_ingestion_config,
    try_build_llm,
    try_build_embedding_client,
)
from ingestion.service import IngestionDeps
from config import EMBEDDING_DIMENSION, EMBEDDING_MODEL, JIRA_BASE_URL, JIRA_TOKEN

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

# Default ticket list used when no ticket IDs are passed on the CLI.
DEFAULT_TICKETS = [
    "IDMT-4320", "IDMT-4335", "IDMT-4447", "IDMT-4482", "IDMT-4491",
    "IDMT-4494", "IDMT-4100", "IDMT-4151", "IDMT-4152", "IDMT-4180",
    "IDMT-11229", "IDMT-21229", "IDMT-21231", "IDMT-30870", "IDMT-30881",
]

TICKETS = DEFAULT_TICKETS  # backward-compat alias

OUTPUT_DIR = Path(os.environ.get("TICKET_OUTPUT_DIR", "ticket_data"))
VERIFY_SSL = False
FORCE_REPROCESS = os.environ.get("FORCE_REPROCESS", "").lower() == "true"
MAX_CONCURRENT = int(os.environ.get("BATCH_MAX_CONCURRENT", "3"))
ENABLE_LLM = os.environ.get("ENABLE_LLM", "true").lower() == "true"
ENABLE_EMBEDDINGS = os.environ.get("ENABLE_EMBEDDINGS", "true").lower() == "true"

SUMMARY_FILENAME = "summary.json"
CHUNKS_FILENAME = "chunks.json"


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)


async def process_ticket(
    ticket_id: str,
    deps: IngestionDeps,
    cfg: Any,
    output_dir: Path,
    force_reprocess: bool,
    mode: str,
) -> tuple[Optional[dict], Optional[list[dict]]]:
    """
    Fetch a ticket and run the requested pipeline(s).

    Returns (summary_doc, chunk_docs) — either may be None depending on mode.
      summary_doc : TicketSummaryDocument.to_index_doc()  dict
      chunk_docs  : HierarchicalTicketResult.all_documents() list
    """
    ticket_dir = output_dir / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    summary_file = ticket_dir / SUMMARY_FILENAME
    chunks_file = ticket_dir / CHUNKS_FILENAME

    need_summary = mode in ("summary", "both")
    need_chunks = mode in ("chunks", "both")

    # --- Skip if already on disk and --no-force ---
    if not force_reprocess:
        summary_ready = summary_file.exists() if need_summary else True
        chunks_ready = chunks_file.exists() if need_chunks else True
        if summary_ready and chunks_ready:
            logger.info("%s already processed — skipping (use --force to reprocess)", ticket_id)
            summary = json.loads(summary_file.read_text("utf-8")) if need_summary else None
            if need_chunks:
                chunks_payload = json.loads(chunks_file.read_text("utf-8"))
                leaf_docs = list(chunks_payload.get("documents") or [])
                section_docs = list(chunks_payload.get("sections") or [])
                chunks = section_docs + leaf_docs
            else:
                chunks = None
            return summary, chunks

    # --- Fetch once from Jira ---
    ticket_data = await deps.jira_client.get_ticket_data(ticket_id, config=cfg)

    summary_doc: Optional[dict] = None
    chunk_docs: Optional[list[dict]] = None

    # --- Summary pipeline ---
    if need_summary:
        from ingestion.summary_pipeline import ingest_ticket_summary_payload
        result = await ingest_ticket_summary_payload(
            ticket_data=ticket_data,
            jira_client=deps.jira_client,
            llm_client=deps.llm_client,
            embedding_client=deps.embedding_client,
            cfg=cfg,
        )
        summary_doc = result.to_index_doc()
        dump_json(summary_doc, summary_file)
        logger.info("%s  summary → %s", ticket_id, summary_file)

    # --- Chunk pipeline ---
    if need_chunks:
        from ingestion.chunk_pipeline import ingest_ticket_chunks_payload
        result = await ingest_ticket_chunks_payload(
            ticket_data=ticket_data,
            jira_client=deps.jira_client,
            llm_client=deps.llm_client,
            embedding_client=deps.embedding_client,
            cfg=cfg,
        )
        section_docs = [doc.to_index_doc() for doc in result.sections]
        leaf_chunk_docs = [doc.to_index_doc() for doc in result.chunks]
        chunk_docs = result.all_documents()
        dump_json(
            {
                "ticket_id": ticket_id,
                "value_stream_ids": result.value_stream_ids,
                "value_stream_names": result.value_stream_names,
                "label_source": result.label_source,
                "section_count": len(result.sections),
                "chunk_count": len(result.chunks),
                "documents": leaf_chunk_docs,
                "sections": section_docs,
            },
            chunks_file,
        )
        logger.info(
            "%s  chunks → %s  (%d sections, %d chunks)",
            ticket_id, chunks_file, len(result.sections), len(result.chunks),
        )

    return summary_doc, chunk_docs


async def run_batch(
    tickets: list[str],
    *,
    output_dir: Path,
    force_reprocess: bool,
    max_concurrent: int,
    enable_llm: bool,
    enable_embeddings: bool,
    mode: str,
    build_faiss: bool = False,
    faiss_dir: Optional[Path] = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = build_ingestion_config(
        llm_model="gpt-5-mini-idp",
        embedding_model=EMBEDDING_MODEL,
        skip_llm_summary=not enable_llm,
        skip_llm_keywords=not enable_llm,
        skip_llm_derived=not enable_llm,
    )

    llm_client = try_build_llm(enable=enable_llm, model="gpt-5-mini-idp")
    embedding_client = try_build_embedding_client(
        enable=enable_embeddings,
        model=EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
    )

    all_summaries: list[dict] = []
    all_chunks: list[dict] = []
    errors: list[dict] = []

    sem = asyncio.Semaphore(max_concurrent)

    async with JiraValueStreamClient(
        base_url=JIRA_BASE_URL,
        token=JIRA_TOKEN,
        verify_ssl=VERIFY_SSL,
    ) as jira_client:
        deps = IngestionDeps(
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
        )

        async def _guarded(tid: str) -> tuple[str, Optional[dict], Optional[list[dict]], Optional[str]]:
            async with sem:
                try:
                    summary, chunks = await process_ticket(
                        ticket_id=tid,
                        deps=deps,
                        cfg=cfg,
                        output_dir=output_dir,
                        force_reprocess=force_reprocess,
                        mode=mode,
                    )
                    return tid, summary, chunks, None
                except Exception as exc:
                    logger.exception("Error processing %s", tid)
                    return tid, None, None, str(exc)

        results = await asyncio.gather(*[_guarded(tid) for tid in tickets])

    for ticket_id, summary, chunks, err in results:
        if err:
            errors.append({"ticket_id": ticket_id, "error": err})
            dump_json({"ticket_id": ticket_id, "error": err}, output_dir / f"ERROR_{ticket_id}.json")
            continue
        if summary is not None:
            all_summaries.append(summary)
        if chunks is not None:
            all_chunks.extend(chunks)

    # --- Aggregate outputs ---
    if mode in ("summary", "both"):
        dump_json(
            {"total": len(all_summaries), "summaries": all_summaries},
            output_dir / "_all_summaries.json",
        )

    if mode in ("chunks", "both"):
        dump_json(
            {"total_chunks": len(all_chunks), "chunks": all_chunks},
            output_dir / "_all_chunks.json",
        )

    if errors:
        dump_json(errors, output_dir / "_errors.json")

    faiss_result: Optional[dict] = None
    if build_faiss:
        try:
            from sinks.faiss_store import build_local_faiss_indexes

            faiss_result = build_local_faiss_indexes(
                output_dir=output_dir,
                index_dir=faiss_dir or (output_dir / "_faiss"),
            )
        except Exception as exc:
            logger.warning("Local FAISS build failed: %s", exc)

    # --- Summary print ---
    print("-" * 80)
    print(f"BATCH COMPLETE — mode={mode}")
    print(f"  Processed : {len(results) - len(errors)} / {len(results)}")
    if errors:
        print(f"  Failed    : {len(errors)}")
    if mode in ("summary", "both"):
        print(f"  Summaries : {output_dir / '_all_summaries.json'} ({len(all_summaries)} docs)")
    if mode in ("chunks", "both"):
        print(f"  Chunks    : {output_dir / '_all_chunks.json'} ({len(all_chunks)} docs)")
    if faiss_result:
        print(
            f"  FAISS     : {faiss_result['index_dir']} "
            f"({faiss_result['summary_doc_count']} summaries, {faiss_result['chunk_doc_count']} chunks)"
        )
    for ticket_id, summary, chunks, err in results:
        if err:
            print(f"  [{ticket_id}] ERROR: {err}")
        else:
            parts = []
            if summary:
                parts.append(f"vs={summary.get('value_stream_names', [])}")
            if chunks is not None:
                parts.append(f"chunks={len(chunks)}")
            print(f"  [{ticket_id}] {', '.join(parts)}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch ingest Jira tickets — summary index (B), chunk index (C), or both.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ingest default tickets — both pipelines:
  python -m pipelines.jira_batch.jobs.batch_ingest_job --mode both

  # Specific tickets, summary only, no LLM:
  python -m pipelines.jira_batch.jobs.batch_ingest_job IDMT-4320 IDMT-4335 --mode summary --no-llm

  # Chunk index only, skip already-done:
  python -m pipelines.jira_batch.jobs.batch_ingest_job --mode chunks --no-force

  # Fast dry-run (no LLM, no embeddings):
  python -m pipelines.jira_batch.jobs.batch_ingest_job IDMT-4320 --mode both --no-llm --no-embeddings
""",
    )

    parser.add_argument(
        "ticket_ids",
        nargs="*",
        metavar="TICKET_ID",
        help="Ticket IDs to process. Omit to use the default hardcoded list.",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "chunks", "both"],
        default="both",
        help="Which pipeline to run (default: both).",
    )

    force_grp = parser.add_mutually_exclusive_group()
    force_grp.add_argument(
        "--force",
        dest="force_reprocess",
        action="store_true",
        default=True,
        help="Reprocess even if output files already exist (default: True).",
    )
    force_grp.add_argument(
        "--no-force",
        dest="force_reprocess",
        action="store_false",
        help="Skip tickets whose output files already exist.",
    )

    parser.add_argument(
        "--output-dir",
        default=str(OUTPUT_DIR),
        metavar="DIR",
        help=f"Root output directory (default: {OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=MAX_CONCURRENT,
        metavar="N",
        help=f"Max concurrent Jira fetches (default: {MAX_CONCURRENT}).",
    )
    parser.add_argument(
        "--no-llm",
        dest="enable_llm",
        action="store_false",
        default=ENABLE_LLM,
        help="Disable LLM calls (heuristic summary only).",
    )
    parser.add_argument(
        "--no-embeddings",
        dest="enable_embeddings",
        action="store_false",
        default=ENABLE_EMBEDDINGS,
        help="Disable embedding generation.",
    )
    parser.add_argument(
        "--build-faiss",
        action="store_true",
        help="Build local LangChain FAISS indexes from the produced summary/chunk JSON artifacts.",
    )
    parser.add_argument(
        "--faiss-dir",
        default=None,
        metavar="DIR",
        help="Output directory for local FAISS indexes (default: <output-dir>/_faiss).",
    )

    args = parser.parse_args()
    tickets = [t.upper() for t in args.ticket_ids] if args.ticket_ids else DEFAULT_TICKETS

    await run_batch(
        tickets,
        output_dir=Path(args.output_dir),
        force_reprocess=args.force_reprocess,
        max_concurrent=args.concurrency,
        enable_llm=args.enable_llm,
        enable_embeddings=args.enable_embeddings,
        mode=args.mode,
        build_faiss=args.build_faiss,
        faiss_dir=Path(args.faiss_dir) if args.faiss_dir else None,
    )


if __name__ == "__main__":
    asyncio.run(main())
