"""
Extract Jira tickets to individual JSON files — no Azure, no enrichment.

Saves raw extraction output (description, attachments, value streams)
to jira_extraction/<TICKET-ID>.json.

Usage:
    python extract_tickets.py IDMT-19761 IDMT-8199
    python extract_tickets.py IDMT-19761 --no-attachments
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from vs_app.modules.rag.corpus.historical_ticket_builder import fetch_tickets

logger = logging.getLogger(__name__)

OUTPUT_DIR = pathlib.Path("jira_extraction")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Extract tickets to jira_extraction/<TICKET-ID>.json",
    )
    parser.add_argument("tickets", nargs="+", help="Ticket IDs (e.g. IDMT-19761)")
    parser.add_argument("--no-attachments", action="store_true", help="Skip attachment extraction")
    parser.add_argument("--output-dir", type=pathlib.Path, default=OUTPUT_DIR, help="Output directory")
    parser.add_argument(
        "--source",
        choices=["jira"],
        default="jira",
        help="Ticket source backend (default: %(default)s).",
    )

    args = parser.parse_args()
    out_dir: pathlib.Path = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting %d ticket(s) -> %s/", len(args.tickets), out_dir)

    raw_tickets = fetch_tickets(
        ticket_ids=args.tickets,
        ticket_source=args.source,
        extract_attachments=not args.no_attachments,
    )

    if not raw_tickets:
        logger.warning("No tickets extracted")
        sys.exit(1)

    for ticket in raw_tickets:
        file_path = out_dir / f"{ticket.ticket_id}.json"
        data = ticket.model_dump()
        file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved %s (%d chars)", file_path, ticket.char_count)

    logger.info("Done — %d ticket(s) saved to %s/", len(raw_tickets), out_dir)


if __name__ == "__main__":
    main()
