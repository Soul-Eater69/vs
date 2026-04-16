from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipelines.historical_rag.pipeline import run_historical_rag_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the merged historical RAG value-stream pipeline on an idea-card text file.",
    )
    parser.add_argument(
        "--input-file",
        required=True,
        metavar="FILE",
        help="Path to a text file containing the idea-card content to classify.",
    )
    parser.add_argument(
        "--fetch-count",
        type=int,
        default=12,
        metavar="N",
        help="Base semantic candidate retrieval count (default: 12).",
    )
    parser.add_argument(
        "--historical-faiss-dir",
        default="ticket_data/_faiss",
        metavar="DIR",
        help="Directory containing local FAISS indexes (default: ticket_data/_faiss).",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Optional JSON output path. Defaults to stdout only.",
    )

    args = parser.parse_args()
    query_text = Path(args.input_file).read_text(encoding="utf-8")
    result = run_historical_rag_pipeline(
        query_text,
        fetch_count=args.fetch_count,
        historical_faiss_dir=args.historical_faiss_dir,
    )

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
