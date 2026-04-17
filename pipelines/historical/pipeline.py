"""
End-to-end historical enrichment pipeline.

    Extract (Jira client) -> Enrich (LLM) -> Store (JSON + Azure Search)

Usage:
    # Fetch from Jira using the canonical ingestion pipeline
    python -m historical.pipeline --tickets IDMT-19761 IDMT-8199

    # Incremental (skip already-enriched)
    python -m historical.pipeline --tickets IDMT-19761 IDMT-8199 --incremental

    # With Azure Search upload
    python -m historical.pipeline --tickets IDMT-19761 IDMT-8199 --upload
"""

from __future__ import annotations

import argparse
import logging
import pathlib
from typing import List, Optional

from .ingestion import enrich_batch, load_json, save_json, upload_to_index
from .ingestion.enrichment import ENRICHMENT_MODEL
from .ingestion.store import DEFAULT_STORE_PATH
from .extractor import fetch_tickets_from_jira
from .models import EnrichedTicket

logger = logging.getLogger(__name__)


def _build_embedding_client() -> Optional[object]:
    try:
        from ...clients.embedding import EmbeddingClient

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
) -> List[EnrichedTicket]:
    """Run the full pipeline: extract -> enrich -> store."""
    out_path = pathlib.Path(output_path)
    if not any(str(ticket_id).strip() for ticket_id in ticket_ids):
        raise ValueError("ticket_ids are required; historical ingestion now always fetches via Jira")

    # --- Extract ---
    logger.info("[PIPELINE] Extracting tickets from Jira")
    raw_tickets = fetch_tickets_from_jira(ticket_ids=ticket_ids)
    logger.info("[PIPELINE] Extracted %d tickets", len(raw_tickets))

    # --- Filter already-enriched ---
    if incremental:
        existing = load_json(out_path)
        done_ids = {t.ticket_id for t in existing if t.enrichment_status == "enriched"}
        before = len(raw_tickets)
        raw_tickets = [t for t in raw_tickets if t.ticket_id not in done_ids]
        logger.info("[PIPELINE] Incremental: %d done, %d remaining", before - len(raw_tickets), len(raw_tickets))

    if not raw_tickets:
        logger.info("[PIPELINE] Nothing to enrich")
        return load_json(out_path)

    # --- Enrich ---
    enriched = enrich_batch(raw_tickets, model=model)

    # --- Store ---
    save_json(enriched, out_path, merge=True)

    # --- Upload ---
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

    args = parser.parse_args()

    run_historical_ingestion(
        ticket_ids=args.tickets,
        output_path=args.output,
        model=args.model,
        incremental=args.incremental,
        upload=args.upload,
        index_name=args.index_name,
    )


if __name__ == "__main__":
    main()
