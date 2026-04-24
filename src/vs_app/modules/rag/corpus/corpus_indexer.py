from __future__ import annotations

import argparse
import logging
import os
import pathlib
from typing import List, Optional

logger = logging.getLogger(__name__)
DEFAULT_STORE_PATH = pathlib.Path("historical_enriched.json")
ENRICHMENT_MODEL = "gpt-4o-mini-idp"


def _build_embedding_client() -> Optional[object]:
    try:
        from vs_app.integrations.clients.embedding import EmbeddingClient

        return EmbeddingClient()
    except Exception as exc:
        logger.warning("[PIPELINE] Embedding client unavailable, uploading without vectors: %s", exc)
        return None


def run_historical_ingestion(
    ticket_ids: List[str],
    output_path: str | pathlib.Path = DEFAULT_STORE_PATH,
    model: str = ENRICHMENT_MODEL,
    incremental: bool = False,
    upload: bool = False,
    index_name: Optional[str] = None,
    ticket_source: str = "jira",
) -> List[object]:
    from .enrichment import enrich_batch
    from .historical_ticket_builder import fetch_tickets
    from .store import load_json, save_json, upload_to_index

    out_path = pathlib.Path(output_path)
    if not any(str(ticket_id).strip() for ticket_id in ticket_ids):
        raise ValueError("ticket_ids are required for historical ingestion")

    logger.info("[PIPELINE] Extracting tickets from %s", ticket_source)
    raw_tickets = fetch_tickets(ticket_ids=ticket_ids, ticket_source=ticket_source)
    logger.info("[PIPELINE] Extracted %d tickets", len(raw_tickets))

    if incremental:
        existing = load_json(out_path)
        done_ids = {ticket.ticket_id for ticket in existing if ticket.enrichment_status == "enriched"}
        before = len(raw_tickets)
        raw_tickets = [ticket for ticket in raw_tickets if ticket.ticket_id not in done_ids]
        logger.info("[PIPELINE] Incremental: %d done, %d remaining", before - len(raw_tickets), len(raw_tickets))

    if not raw_tickets:
        logger.info("[PIPELINE] Nothing to enrich")
        return load_json(out_path)

    enriched = enrich_batch(raw_tickets, model=model)
    save_json(enriched, out_path, merge=True)

    if upload:
        upload_to_index(
            enriched,
            index_name=index_name or "historical-enriched-tickets",
            embedding_client=_build_embedding_client(),
        )

    return load_json(out_path)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Historical ticket enrichment: extract -> enrich -> store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python -m historical.pipeline --tickets IDMT-19761 IDMT-8199
  python -m historical.pipeline --tickets IDMT-19761 --upload
  python -m historical.pipeline --tickets IDMT-19761 IDMT-8199 --incremental
""",
    )
    parser.add_argument("--tickets", nargs="+", required=True, help="Ticket IDs")
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_STORE_PATH, help="Output JSON path")
    parser.add_argument("--model", default=ENRICHMENT_MODEL, help="LLM model")
    parser.add_argument("--incremental", action="store_true", help="Skip already-enriched tickets")
    parser.add_argument("--upload", action="store_true", help="Upload to Azure Search")
    parser.add_argument("--index-name", default=None, help="Azure Search index name")
    parser.add_argument(
        "--source",
        choices=["jira", "neo4j"],
        default=os.environ.get("INGESTION_TICKET_SOURCE", "jira"),
        help="Ticket source backend (default: %(default)s).",
    )

    args = parser.parse_args()

    run_historical_ingestion(
        ticket_ids=args.tickets,
        output_path=args.output,
        model=args.model,
        incremental=args.incremental,
        upload=args.upload,
        index_name=args.index_name,
        ticket_source=args.source,
    )


if __name__ == "__main__":
    main()
