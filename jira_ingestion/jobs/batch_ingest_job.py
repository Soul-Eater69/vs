from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from jira_ingestion import JiraValueStreamClient
from jira_ingestion.projections.jira_record_projection import project_ticket_outputs
from jira_ingestion.runtime.runtime_factory import (
    build_ingestion_config,
    try_build_llm,
    try_build_embedding_client,
)
from ingestion.ticket_ingestion_service import IngestionDeps, ingest_one_ticket
from src.config import EMBEDDING_DIMENSION, EMBEDDING_MODEL, JIRA_BASE_URL, JIRA_TOKEN

# Default ticket list used when no ticket IDs are passed on the CLI.
DEFAULT_TICKETS = [
    "IDMT-4320", "IDMT-4335", "IDMT-4447", "IDMT-4482", "IDMT-4491",
    "IDMT-4494", "IDMT-4100", "IDMT-4151", "IDMT-4152", "IDMT-4180",
    "IDMT-11229", "IDMT-21229", "IDMT-21231", "IDMT-30870", "IDMT-30881",
]

# Keep a module-level alias so external callers that imported TICKETS still work.
TICKETS = DEFAULT_TICKETS

OUTPUT_DIR = Path(os.environ.get("TICKET_CHUNKS_DIR", "ticket_chunks"))
VERIFY_SSL = False
FORCE_REPROCESS = os.environ.get("FORCE_REPROCESS", "").lower() == "true"
MAX_CONCURRENT = int(os.environ.get("BATCH_MAX_CONCURRENT", "3"))
ENABLE_LLM = os.environ.get("ENABLE_LLM", "true").lower() == "true"
ENABLE_EMBEDDINGS = os.environ.get("ENABLE_EMBEDDINGS", "true").lower() == "true"

LEGACY_CHUNKS_FILENAME = "07_chunks.json"
LEGACY_VS_FILENAME = "08_valuestream_map.json"
SIMPLE_CHUNKS_FILENAME = "chunks.json"
SIMPLE_VS_FILENAME = "mappings.json"


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, indent=2, ensure_ascii=False, default=str)


async def process_ticket(
    ticket_id: str,
    deps: IngestionDeps,
    cfg: Any,
    output_dir: Path,
    force_reprocess: bool,
    write_simple_filenames: bool,
) -> tuple[list[dict], dict]:
    ticket_dir = output_dir / ticket_id
    ticket_dir.mkdir(parents=True, exist_ok=True)

    chunks_file = ticket_dir / LEGACY_CHUNKS_FILENAME
    vs_file = ticket_dir / LEGACY_VS_FILENAME

    if not force_reprocess and chunks_file.exists() and vs_file.exists():
        chunks = json.loads(chunks_file.read_text(encoding="utf-8")).get("chunks", [])
        vs_map = json.loads(vs_file.read_text(encoding="utf-8"))
        return chunks, vs_map

    result = await ingest_one_ticket(ticket_id=ticket_id, deps=deps, cfg=cfg)
    if not isinstance(result, dict) or "ticket_key" not in result:
        print(f"\n[ERROR] Ticket {ticket_id} result missing 'ticket_key'. Type: {type(result)} Value: {repr(result)}\n", flush=True)
        raise RuntimeError(f"Ticket {ticket_id} result missing 'ticket_key'. See above for details.")

    chunk_records, vs_record = project_ticket_outputs(ticket_id, result, include_jira_url=True)

    dump_json(
        {
            "ticket_id": ticket_id,
            "mapped_value_stream_ids": vs_record.get("valueStreamIds", []),
            "mapped_value_stream_names": vs_record.get("valueStreamNames", []),
            "chunk_count": len(chunk_records),
            "chunks": chunk_records,
        },
        chunks_file,
    )

    dump_json(vs_record, vs_file)

    if write_simple_filenames:
        dump_json(chunk_records, ticket_dir / SIMPLE_CHUNKS_FILENAME)
        # some tools prefer SIMPLE_VS_FILENAME
        dump_json(vs_record, ticket_dir / SIMPLE_VS_FILENAME)

    return chunk_records, vs_record


async def update_faiss_index(
    ticket_ids: list[str],
    chunks_dir: str,
    index_dir: str,
    model: str,
    rebuild: bool,
) -> bool:
    """
    Update (or rebuild) the local FAISS summary index for the given ticket IDs.
    Returns True on success.
    """
    try:
        from summary_rag.ingestion.faiss_indexer import ingest_tickets_to_index, DEFAULT_INDEX_DIR
        effective_index_dir = index_dir or DEFAULT_INDEX_DIR

        ok = ingest_tickets_to_index(
            ticket_ids=ticket_ids,
            chunks_dir=Path(chunks_dir),
            index_dir=Path(effective_index_dir),
            model=model,
            include_chunk_embeddings=rebuild,
            use_existing_summaries=not rebuild,
        )
        return ok
    except Exception as exc:
        print(f"[ERROR] FAISS index update failed: {exc}")
        return False


async def run_batch(
    tickets: list[str],
    *,
    output_dir: Path,
    force_reprocess: bool,
    max_concurrent: int,
    enable_llm: bool,
    enable_embeddings: bool,
    update_index: bool,
    rebuild_index: bool,
    index_dir: str,
    index_model: str,
    write_simple_filenames: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = build_ingestion_config(
        llm_model="gpt-5-mini-idp",
        embedding_model=EMBEDDING_MODEL,
        skip_llm_summary=not enable_llm,
        skip_llm_keywords=not enable_llm,
        skip_llm_derived=not enable_llm,
    )

    llm_client = try_build_llm(enable_llm, model="gpt-5-mini-idp")
    embedding_client = try_build_embedding_client(
        enable_embeddings,
        model=EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
    )

    all_chunks: list[dict] = []
    all_vs_maps: list[dict] = []
    errors: list[dict] = []
    succeeded_ids: list[str] = []

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

        async def _guarded_process(tid: str) -> tuple[str, Optional[list[dict]], Optional[dict], Optional[str]]:
            async with sem:
                try:
                    chunks, vs_map = await process_ticket(
                        ticket_id=tid,
                        deps=deps,
                        cfg=cfg,
                        output_dir=output_dir,
                        force_reprocess=force_reprocess,
                        write_simple_filenames=write_simple_filenames,
                    )
                    return tid, chunks, vs_map, None
                except Exception as exc:
                    return tid, None, None, str(exc)

        results = await asyncio.gather(*[_guarded_process(tid) for tid in tickets])

    for ticket_id, chunks, vs_map, err in results:
        if err:
            errors.append({"ticket_id": ticket_id, "error": err})
            dump_json({"ticket_id": ticket_id, "error": err}, output_dir / f"ERROR_{ticket_id}.json")
            continue
        succeeded_ids.append(ticket_id)
        all_chunks.extend(chunks or [])
        if vs_map is not None:
            all_vs_maps.append(vs_map)

    dump_json(
        {
            "total_chunks": len(all_chunks),
            "tickets_processed": len(all_vs_maps),
            "tickets_failed": len(errors),
            "chunks": all_chunks,
        },
        output_dir / "_all_chunks.json",
    )

    dump_json(
        {
            "total_tickets": len(all_vs_maps),
            "tickets_with_vs_links": sum(1 for m in all_vs_maps if m["valueStreamIds"]),
            "tickets_with_products": sum(1 for m in all_vs_maps if m["impactedProducts"]),
            "maps": all_vs_maps,
        },
        output_dir / "_all_valuestream_maps.json",
    )

    if errors:
        dump_json(errors, output_dir / "_errors.json")

    # -- Summary --
    print("-" * 80)
    print(f"BATCH COMPLETE - {len(all_vs_maps)} succeeded, {len(errors)} failed")
    print(f"Chunk Index: {output_dir / '_all_chunks.json'}")
    print(f"VS Maps:     {output_dir / '_all_valuestream_maps.json'}")
    for vs in all_vs_maps:
        vs_id = vs["ticketId"]
        chunks = [c for c in all_chunks if c.get("sourceUrl") == vs_id]
        print(f"  [{vs_id}] chunks={len(chunks)}, vs={len(vs['valueStreamIds'])}")

    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  [{e['ticket_id']}]: {e['error']}")

    # -- Optional FAISS index update --
    if (update_index or rebuild_index) and succeeded_ids:
        if rebuild_index:
            # Rebuild over ALL tickets present in ticket_chunks/, not just this batch
            all_existing = sorted([
                d.name for d in output_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".") and (d / LEGACY_CHUNKS_FILENAME).exists()
            ])
            index_tickets = all_existing
            print(f"\n[FAISS] --rebuild-index: indexing all {len(index_tickets)} tickets in {output_dir}")
        else:
            # Only update the tickets we just processed
            index_tickets = succeeded_ids
            print(f"\n[FAISS] --update-index: indexing {len(index_tickets)} newly processed tickets")

        ok = await update_faiss_index(
            ticket_ids=index_tickets,
            chunks_dir=str(output_dir),
            index_dir=index_dir,
            model=index_model,
            rebuild=rebuild_index,  # rebuild-index forces regeneration of summaries
        )
        print(f"[FAISS] Index update: {'SUCCESS' if ok else 'FAILED'}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch ingest one or more Jira tickets into ticket_chunks/, optionally "
                    "updating the FAISS summary RAG index afterwards."
    )
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = """
Examples:
  # Ingest specific tickets (force reprocess):
  python -m jira_ingestion.jobs.batch_ingest_job IDMT-31170 IDMT-9999

  # Process the default list without forcing:
  python -m jira_ingestion.jobs.batch_ingest_job --no-force

  # Process a ticket AND update the FAISS index for those tickets:
  python -m jira_ingestion.jobs.batch_ingest_job IDMT-31170 --update-index

  # Process a ticket and rebuild the FAISS index from scratch over all chunks:
  python -m jira_ingestion.jobs.batch_ingest_job IDMT-31170 --rebuild-index

  # Also write chunks.json and mappings.json alongside the legacy
  python -m jira_ingestion.jobs.batch_ingest_job --force --simple-filenames

  # Rebuild fresh chunks for all default tickets and overwrite existing outputs:
  python -m jira_ingestion.jobs.batch_ingest_job --force --simple-filenames
"""

    # Ticket selection
    parser.add_argument(
        "ticket_ids",
        nargs="*",
        metavar="TICKET_ID",
        help="Ticket IDs to process. Omit to use the default hardcoded list.",
    )

    # Reprocess control
    force_grp = parser.add_mutually_exclusive_group()
    force_grp.add_argument(
        "--force",
        dest="force_reprocess",
        action="store_true",
        default=True,
        help="Force reprocess even if 07_chunks.json already exists (default: True).",
    )
    force_grp.add_argument(
        "--no-force",
        dest="force_reprocess",
        action="store_false",
        help="Skip tickets that already have both 07_chunks.json and 08_valuestream_map.json.",
    )

    # FAISS index
    idx_grp = parser.add_mutually_exclusive_group()
    idx_grp.add_argument(
        "--update-index",
        action="store_true",
        default=False,
        help="After chunking, update the FAISS summary index for the tickets "
             "that were successfully processed.",
    )
    idx_grp.add_argument(
        "--rebuild-index",
        action="store_true",
        default=False,
        help="After chunking, rebuild the FAISS summary index from scratch, "
             "scanning ALL tickets present in --output-dir.",
    )

    parser.add_argument(
        "--index-dir",
        default="",
        metavar="DIR",
        help="FAISS index directory (default: local ticket summary folder).",
    )
    parser.add_argument(
        "--index-model",
        default="gpt-5-mini-idp",
        metavar="MODEL",
        help="LLM model used when generating ticket summaries for the FAISS index.",
    )

    # Infra overrides
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("TICKET_CHUNKS_DIR", "ticket_chunks"),
        metavar="DIR",
        help="Root directory to write ticket chunks (default: ticket_chunks/).",
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
        help="Disable LLM calls during ingestion.",
    )
    parser.add_argument(
        "--no-embeddings",
        dest="enable_embeddings",
        action="store_false",
        default=ENABLE_EMBEDDINGS,
        help="Disable embedding generation during ingestion.",
    )
    parser.add_argument(
        "--simple-filenames",
        action="store_true",
        default=False,
        help="Also write chunks.json and mappings.json alongside the legacy "
             "07_chunks.json and 08_valuestream_map.json files.",
    )

    args = parser.parse_args()

    tickets = [t.upper() for t in args.ticket_ids] if args.ticket_ids else DEFAULT_TICKETS
    # --force defaults to True unless --no-force is explicitly passed
    force_reprocess = args.force_reprocess if args.force_reprocess is not None else FORCE_REPROCESS

    await run_batch(
        tickets,
        output_dir=Path(args.output_dir),
        force_reprocess=force_reprocess,
        max_concurrent=args.concurrency,
        enable_llm=args.enable_llm,
        enable_embeddings=args.enable_embeddings,
        update_index=args.update_index,
        rebuild_index=args.rebuild_index,
        index_dir=args.index_dir,
        index_model=args.index_model,
        write_simple_filenames=args.simple_filenames,
    )


if __name__ == "__main__":
    asyncio.run(main())