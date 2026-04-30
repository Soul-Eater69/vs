"""Batch ingest Jira tickets into historical RAG summary artifacts.

This is the root-level replacement for the old batch_ingest_job summary path.
It writes:

  ticket_data/<TICKET-ID>/summary.json
  ticket_data/_all_summaries.json

Optionally it also rebuilds:

  ticket_data/_faiss/

Usage:
  py -3 jobs/ingest_tickets.py IDMT-4491 --build-faiss
  py -3 jobs/ingest_tickets.py --input-ticket-ids ticket_ids.txt --concurrency 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from vs_app.container import build_ticket_fetcher
from vs_app.integrations.jira.fetch_compat import get_ticket_data_compat
from vs_app.jobs.jira_batch.runtime.runtime_factory import (
    build_ingestion_config,
    try_build_embedding_client,
    try_build_llm,
)
from vs_app.modules.ingestion.summary.pipeline import ingest_ticket_summary_payload
from vs_app.settings import EMBEDDING_DIMENSION, EMBEDDING_MODEL


logger = logging.getLogger(__name__)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    ticket_ids = resolve_ticket_ids(
        positional=args.ticket_ids,
        input_ticket_ids=args.input_ticket_ids,
        limit=args.limit,
    )
    if not ticket_ids:
        raise SystemExit("No ticket IDs provided.")

    asyncio.run(
        run_batch(
            ticket_ids=ticket_ids,
            output_dir=Path(args.output_dir),
            source=args.source,
            concurrency=args.concurrency,
            force=args.force,
            enable_llm=not args.no_llm,
            enable_embeddings=not args.no_embeddings,
            build_faiss=args.build_faiss,
            faiss_dir=Path(args.faiss_dir),
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch ingest Jira tickets into ticket_data/<ticket>/summary.json artifacts.",
    )
    parser.add_argument("ticket_ids", nargs="*", metavar="TICKET_ID")
    parser.add_argument(
        "--input-ticket-ids",
        default=None,
        help="Text or JSON file containing ticket IDs.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", default="ticket_data")
    parser.add_argument("--source", choices=["jira"], default="jira")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="Reprocess tickets with existing summary.json.")
    parser.add_argument("--no-llm", action="store_true", help="Use heuristic summaries only.")
    parser.add_argument("--no-embeddings", action="store_true", help="Do not write summary_embedding.")
    parser.add_argument("--build-faiss", action="store_true", help="Rebuild FAISS after summaries are written.")
    parser.add_argument("--faiss-dir", default="ticket_data/_faiss")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


async def run_batch(
    *,
    ticket_ids: list[str],
    output_dir: Path,
    source: str,
    concurrency: int,
    force: bool,
    enable_llm: bool,
    enable_embeddings: bool,
    build_faiss: bool,
    faiss_dir: Path,
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

    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[tuple[str, dict | None, str | None]] = []

    async with build_ticket_fetcher(source=source, verify_ssl=False) as jira_client:

        async def guarded(ticket_id: str) -> tuple[str, dict | None, str | None]:
            async with semaphore:
                try:
                    summary = await ingest_one_ticket(
                        ticket_id=ticket_id,
                        output_dir=output_dir,
                        jira_client=jira_client,
                        llm_client=llm_client,
                        embedding_client=embedding_client,
                        cfg=cfg,
                        force=force,
                    )
                    return ticket_id, summary, None
                except Exception as exc:
                    logger.exception("Failed to ingest %s", ticket_id)
                    return ticket_id, None, str(exc)

        results = await asyncio.gather(*(guarded(ticket_id) for ticket_id in ticket_ids))

    summaries: list[dict] = []
    errors: list[dict[str, str]] = []
    for ticket_id, summary, error in results:
        if error:
            errors.append({"ticket_id": ticket_id, "error": error})
            write_json(output_dir / f"ERROR_{ticket_id}.json", {"ticket_id": ticket_id, "error": error})
            continue
        if summary is not None:
            summaries.append(summary)

    write_json(
        output_dir / "_all_summaries.json",
        {
            "total": len(summaries),
            "summaries": summaries,
        },
    )
    if errors:
        write_json(output_dir / "_errors.json", errors)

    faiss_result: dict[str, Any] | None = None
    if build_faiss:
        from vs_app.integrations.sinks.faiss_store import build_local_faiss_indexes

        faiss_result = build_local_faiss_indexes(
            output_dir=output_dir,
            index_dir=faiss_dir,
        )

    print("")
    print("Batch ingest complete")
    print(f"  processed:       {len(summaries)} / {len(ticket_ids)}")
    print(f"  failed:          {len(errors)}")
    print(f"  summaries:       {output_dir / '_all_summaries.json'}")
    if faiss_result:
        print(f"  faiss:           {faiss_result['index_dir']}")
        print(f"  faiss summaries: {faiss_result['summary_doc_count']}")
    for ticket_id, summary, error in results:
        if error:
            print(f"  [{ticket_id}] ERROR: {error}")
        else:
            names = list((summary or {}).get("value_stream_names") or [])
            print(f"  [{ticket_id}] summary.json value_streams={names}")


async def ingest_one_ticket(
    *,
    ticket_id: str,
    output_dir: Path,
    jira_client: Any,
    llm_client: Any,
    embedding_client: Any,
    cfg: Any,
    force: bool,
) -> dict:
    ticket_dir = output_dir / ticket_id
    summary_path = ticket_dir / "summary.json"
    if summary_path.exists() and not force:
        return read_json(summary_path)

    ticket_data = await get_ticket_data_compat(
        jira_client,
        ticket_id,
        config=cfg,
        llm_client=llm_client,
    )
    result = await ingest_ticket_summary_payload(
        ticket_data=ticket_data,
        jira_client=jira_client,
        llm_client=llm_client,
        embedding_client=embedding_client,
        cfg=cfg,
    )
    summary_doc = result.to_index_doc()
    write_json(summary_path, summary_doc)
    logger.info("Wrote %s", summary_path)
    return summary_doc


def resolve_ticket_ids(
    *,
    positional: list[str],
    input_ticket_ids: str | None,
    limit: int | None,
) -> list[str]:
    values: list[str] = []
    values.extend(positional or [])
    if input_ticket_ids:
        values.extend(load_ticket_ids(Path(input_ticket_ids)))

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticket_id = str(value or "").strip().upper()
        if not ticket_id or ticket_id in seen:
            continue
        seen.add(ticket_id)
        out.append(ticket_id)
    if limit is not None:
        return out[: max(0, limit)]
    return out


def load_ticket_ids(path: Path) -> list[str]:
    if not path.exists():
        raise SystemExit(f"Ticket ID file not found: {path}")

    if path.suffix.lower() == ".json":
        payload = read_json(path)
        if isinstance(payload, list):
            return [str(value) for value in payload]
        if isinstance(payload, dict):
            for key in ("ticket_ids", "tickets", "ids"):
                values = payload.get(key)
                if isinstance(values, list):
                    return [str(value) for value in values]
        raise SystemExit(f"Could not find ticket IDs in JSON file: {path}")

    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        ids.extend(part.strip() for part in clean.replace(",", " ").split() if part.strip())
    return ids


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())

