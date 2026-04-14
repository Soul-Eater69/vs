"""
Simplified batch ingestion job.

Usage:
  python -m ingestion_v2.batch IDMT-19761 IDMT-4320
  python -m ingestion_v2.batch --no-llm --no-embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from jira_ingestion import JiraValueStreamClient
from jira_ingestion.runtime.runtime_factory import try_build_llm, try_build_embedding_client
from src.config import EMBEDDING_DIMENSION, EMBEDDING_MODEL, JIRA_BASE_URL, JIRA_TOKEN

from ingestion_v2.pipeline import ingest_ticket, project_chunk_records, project_vs_record

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_TICKETS = [
    "IDMT-4320", "IDMT-4335", "IDMT-4447", "IDMT-4482", "IDMT-4491",
    "IDMT-4494", "IDMT-4100", "IDMT-4151", "IDMT-4152", "IDMT-4180",
    "IDMT-11229", "IDMT-21229", "IDMT-21231", "IDMT-30870", "IDMT-30881",
]

OUTPUT_DIR = Path(os.environ.get("TICKET_CHUNKS_DIR", "ticket_chunks"))
MAX_CONCURRENT = int(os.environ.get("BATCH_MAX_CONCURRENT", "3"))


def dump_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


async def process_one(
    ticket_id: str,
    jira_client: Any,
    llm_client: Any,
    embedding_client: Any,
    sharepoint_client: Any,
    output_dir: Path,
    skip_llm: bool,
    skip_embeddings: bool,
) -> tuple[str, list[dict] | None, dict | None, str | None]:
    """Process a single ticket. Returns (ticket_id, chunks, vs_record, error)."""
    try:
        result = await ingest_ticket(
            ticket_id=ticket_id,
            jira_client=jira_client,
            llm_client=llm_client,
            embedding_client=embedding_client,
            sharepoint_client=sharepoint_client,
            skip_llm=skip_llm,
            skip_embeddings=skip_embeddings,
        )

        # Write per-ticket output
        ticket_dir = output_dir / ticket_id
        dump_json(result, ticket_dir / "result.json")

        # Project to index-ready formats
        chunk_records = project_chunk_records(ticket_id, result, jira_base_url=JIRA_BASE_URL)
        vs_record = project_vs_record(ticket_id, result)

        dump_json({"chunks": chunk_records}, ticket_dir / "chunks.json")
        dump_json(vs_record, ticket_dir / "mappings.json")

        return ticket_id, chunk_records, vs_record, None

    except Exception as exc:
        logger.error("Failed %s: %s", ticket_id, exc)
        return ticket_id, None, None, str(exc)


def _try_build_sharepoint_client():
    """Build SharePoint client if Graph credentials are available."""
    try:
        from sharepoint import SharePointClient
        # SharePointClient needs a config object; check if env vars exist first
        graph_client_id = os.environ.get("GRAPH_CLIENT_ID")
        if not graph_client_id:
            logger.info("No GRAPH_CLIENT_ID — SharePoint downloads disabled")
            return None
        # SharePointClient expects a config with logging + sharepoint settings.
        # For lightweight usage we only need get_file_content_by_url which just
        # needs access_token + resource_url, so we build a minimal config.
        from types import SimpleNamespace
        cfg = SimpleNamespace(
            logging=SimpleNamespace(
                logger_name="sharepoint",
                logging_level="WARNING",
                log_file_path=None,
            ),
            delia=SimpleNamespace(
                sharepoint=SimpleNamespace(
                    resource_url="https://graph.microsoft.com/",
                    oauth_endpoint="https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                ),
            ),
        )
        client = SharePointClient(cfg)
        if client.access_token:
            logger.info("SharePoint client ready")
            return client
        logger.warning("SharePoint client failed to get access token")
        return None
    except Exception as exc:
        logger.info("SharePoint client unavailable: %s", exc)
        return None


async def run_batch(
    tickets: list[str],
    output_dir: Path,
    max_concurrent: int = 3,
    skip_llm: bool = False,
    skip_embeddings: bool = False,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    llm_client = try_build_llm(enable=not skip_llm, model="gpt-5-mini-idp")
    embedding_client = try_build_embedding_client(
        enable=not skip_embeddings,
        model=EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
    )
    sharepoint_client = _try_build_sharepoint_client()

    sem = asyncio.Semaphore(max_concurrent)

    async with JiraValueStreamClient(
        base_url=JIRA_BASE_URL,
        token=JIRA_TOKEN,
        verify_ssl=False,
    ) as jira_client:

        async def guarded(tid: str):
            async with sem:
                return await process_one(
                    tid, jira_client, llm_client, embedding_client,
                    sharepoint_client, output_dir, skip_llm, skip_embeddings,
                )

        results = await asyncio.gather(*[guarded(tid) for tid in tickets])

    # Aggregate
    all_chunks: list[dict] = []
    all_vs: list[dict] = []
    errors: list[dict] = []

    for ticket_id, chunks, vs_record, err in results:
        if err:
            errors.append({"ticket_id": ticket_id, "error": err})
            continue
        all_chunks.extend(chunks or [])
        if vs_record:
            all_vs.append(vs_record)

    dump_json({"total": len(all_chunks), "chunks": all_chunks}, output_dir / "_all_chunks.json")
    dump_json({"total": len(all_vs), "maps": all_vs}, output_dir / "_all_valuestream_maps.json")

    # Summary
    print("=" * 70)
    print(f"BATCH COMPLETE — {len(all_vs)} succeeded, {len(errors)} failed")
    print(f"  Chunks: {output_dir / '_all_chunks.json'}")
    print(f"  VS maps: {output_dir / '_all_valuestream_maps.json'}")

    for vs in all_vs:
        tid = vs["ticketId"]
        n_chunks = sum(1 for c in all_chunks if c.get("sourceId") == tid)
        n_vs = len(vs.get("valueStreamIds", []))
        print(f"  [{tid}] chunks={n_chunks}, vs={n_vs}")

    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  {e['ticket_id']}: {e['error']}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch ingest Jira tickets (v2)")
    parser.add_argument("ticket_ids", nargs="*", help="Ticket IDs (default: hardcoded list)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENT)
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls")
    parser.add_argument("--no-embeddings", action="store_true", help="Skip embedding generation")
    args = parser.parse_args()

    tickets = [t.upper() for t in args.ticket_ids] if args.ticket_ids else DEFAULT_TICKETS

    asyncio.run(run_batch(
        tickets=tickets,
        output_dir=Path(args.output_dir),
        max_concurrent=args.concurrency,
        skip_llm=args.no_llm,
        skip_embeddings=args.no_embeddings,
    ))


if __name__ == "__main__":
    main()
