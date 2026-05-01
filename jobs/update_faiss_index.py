"""Build or refresh the local historical FAISS indexes.

Usage:
  py -3 jobs/update_faiss_index.py
  py -3 jobs/update_faiss_index.py --input-dir ticket_data --index-dir ticket_data/_faiss
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


logger = logging.getLogger(__name__)


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    input_dir = Path(args.input_dir)
    index_dir = Path(args.index_dir)
    validate_input_dir(input_dir)

    summary_count = count_summary_artifacts(input_dir)
    chunk_count = count_chunk_artifacts(input_dir)
    if summary_count == 0 and not args.allow_empty:
        raise SystemExit(
            f"No summary artifacts found in {input_dir}. Expected "
            "summaries.json, _all_summaries.json, or <ticket>/summary.json."
        )

    print(f"Input:       {input_dir}")
    print(f"Index dir:   {index_dir}")
    print(f"Summaries:   {summary_count}")
    print(f"Chunks:      {chunk_count}")
    print("Building FAISS indexes...")

    from vs_app.integrations.sinks.faiss_store import build_local_faiss_indexes

    result = build_local_faiss_indexes(
        output_dir=input_dir,
        index_dir=index_dir,
    )

    print("")
    print("FAISS build complete")
    print(f"  index_dir:       {result['index_dir']}")
    print(f"  source summaries:{result.get('source_summary_count', result['summary_doc_count'])}")
    print(f"  summary_docs:    {result['summary_doc_count']}")
    print(f"  skipped:         {result.get('skipped_summary_count', 0)}")
    print(f"  chunk_docs:      {result['chunk_doc_count']}")
    print(f"  manifest:        {index_dir / 'manifest.json'}")
    print(f"  summary index:   {index_dir / 'summaries'}")
    if result.get("chunk_doc_count", 0):
        print(f"  chunk index:     {index_dir / 'chunks'}")
    skipped = result.get("skipped_summary_docs") or []
    if skipped:
        print("  skipped ids:")
        for row in skipped:
            print(f"    {row.get('ticket_id') or '<missing ticket_id>'}: {row.get('reason')}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build local FAISS indexes from ticket summary/chunk artifacts.",
    )
    parser.add_argument(
        "--input-dir",
        default="ticket_data",
        help="Directory containing summaries.json or compatible summary artifacts.",
    )
    parser.add_argument(
        "--index-dir",
        default="ticket_data/_faiss",
        help="Directory where FAISS indexes and summary_docs.json are written.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow a build attempt even when no summary artifacts are found.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def validate_input_dir(input_dir: Path) -> None:
    if not input_dir.exists():
        raise SystemExit(f"Input directory not found: {input_dir}")
    if not input_dir.is_dir():
        raise SystemExit(f"Input path is not a directory: {input_dir}")


def count_summary_artifacts(input_dir: Path) -> int:
    for aggregate in (input_dir / "summaries.json", input_dir / "_all_summaries.json"):
        if not aggregate.exists():
            continue
        payload = read_json(aggregate)
        if isinstance(payload, dict) and isinstance(payload.get("summaries"), list):
            return len(payload["summaries"])
        if isinstance(payload, list):
            return len(payload)
    return sum(1 for _ in input_dir.glob("*/summary.json"))


def count_chunk_artifacts(input_dir: Path) -> int:
    aggregate = input_dir / "_all_chunks.json"
    if aggregate.exists():
        payload = read_json(aggregate)
        if isinstance(payload, dict):
            chunks = payload.get("chunks")
            if isinstance(chunks, list):
                return len(chunks)
        if isinstance(payload, list):
            return len(payload)

    total = 0
    for path in input_dir.glob("*/chunks.json"):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        total += len(payload.get("documents") or [])
        total += len(payload.get("sections") or [])
    return total


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    raise SystemExit(main())
