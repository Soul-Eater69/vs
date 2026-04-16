from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipelines.historical_rag.pipeline import run_historical_rag_pipeline
from processing.extraction import extract_idea_card_text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the merged historical RAG value-stream pipeline on a local idea-card file or doc_id.",
    )
    parser.add_argument(
        "--input-file",
        metavar="FILE",
        help="Path to an idea-card file (.txt, .md, .pdf, .pptx, .docx, etc.) to classify.",
    )
    parser.add_argument(
        "--doc-id",
        default=None,
        metavar="ID",
        help="Optional doc_id to resolve from --idea-cards-dir using the legacy {doc_id}.* lookup.",
    )
    parser.add_argument(
        "--idea-cards-dir",
        default="idea_cards",
        metavar="DIR",
        help="Directory used with --doc-id lookup (default: idea_cards).",
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
    if not args.input_file and not args.doc_id:
        parser.error("Provide either --input-file or --doc-id")

    query_text = extract_idea_card_text(
        args.input_file,
        doc_id=args.doc_id,
        idea_cards_dir=args.idea_cards_dir,
    )
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
