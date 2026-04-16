"""
Historical ticket enrichment pipeline.

    Extract (Jira client) -> Enrich (LLM) -> Store (JSON + Azure Search)

Each historical ticket gets:
- A concise business summary
- Domain tags
- Each attached value stream classified as "direct" or "implied"

Usage:
    from historical import run_historical_ingestion

    run_historical_ingestion(ticket_ids=["IDMT-19761"])
"""

from .pipeline import run_historical_ingestion

__all__ = ["run_historical_ingestion"]
