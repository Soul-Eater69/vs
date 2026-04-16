from ingestion.chunk_pipeline import (
    ingest_ticket_chunks,
    ingest_ticket_chunks_payload,
)
from ingestion.idea_card import IdeaCard, resolve_idea_card
from ingestion.pipeline import (
    IngestionMode,
    IngestionResult,
    ingest_ticket,
    ingest_ticket_payload,
)
from ingestion.service import (
    IngestionDeps,
    TicketIngestionContext,
    ingest_one_ticket,
    ingest_single_ticket,
)
from ingestion.summary_pipeline import (
    ingest_ticket_summary,
    ingest_ticket_summary_payload,
)

__all__ = [
    # Router (mode-based)
    "ingest_ticket",
    "ingest_ticket_payload",
    "IngestionMode",
    "IngestionResult",
    # Direct pipelines
    "ingest_ticket_summary",
    "ingest_ticket_summary_payload",
    "ingest_ticket_chunks",
    "ingest_ticket_chunks_payload",
    # Shared primitives
    "IdeaCard",
    "resolve_idea_card",
    # Service wrappers
    "TicketIngestionContext",
    "IngestionDeps",
    "ingest_single_ticket",
    "ingest_one_ticket",
]
