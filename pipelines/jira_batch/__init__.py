"""
Jira Ingestion - Jira ticket ingestion pipeline.

Public contract
-----------------------------------------------------------------------------

Framework layer (normal callers)
    from pipelines.jira_batch import (
        JiraIngestionConfig,   # all thresholds, field mapping, persistence toggles
        JiraTicketClient,      # authenticated Jira access
        ingest_ticket,         # primary operational entry point
    )

Stable schema types
    from pipelines.jira_batch import (
        TicketInput,           # expected shape of get_ticket_data() response
        PipelineDocument,      # top-level output of assemble_document()
        PreChunkDocument,      # 04_assembled_prechunk.json artifact shape
        ChunkRecord,           # individual content chunk
        TriageArtifact,        # 03_triage_output.json artifact shape
    )

Debug artifacts contract
-----------------------------------------------------------------------------
When `storage_dir` is supplied to `ingest_ticket`, the following files
are written automatically to `<storage_dir>/debug/<ticket_key>/`:

    01_raw_ticket.json          - raw Jira API payload
    02_attachment_contents.json - per-attachment inventory + triage scores
    03_triage_output.json       - primary/supplementary decision + multi-score
    04_assembled_prechunk.json  - canonical pre-chunk document for inspection
    05_debug_report.json        - compact summary of all pipeline decisions
"""

from ingestion import (
    IngestionDeps,
    TicketIngestionContext,
    ingest_one_ticket,
    ingest_single_ticket,
    ingest_ticket,
    ingest_ticket_payload,
)
from ingestion.adapters.jira import JiraTicketClient
from .config import JiraIngestionConfig
from .models import (
    ChunkRecord,
    PipelineDocument,
    PreChunkDocument,
    TicketInput,
    TriageArtifact,
)


def build_ingestion_config(*args, **kwargs):
    from .runtime.runtime_factory import build_ingestion_config as _impl

    return _impl(*args, **kwargs)

__all__ = [
    # Framework layer
    "JiraIngestionConfig",
    "JiraTicketClient",
    "ingest_ticket",
    "ingest_ticket_payload",
    "TicketIngestionContext",
    "IngestionDeps",
    "ingest_single_ticket",
    "ingest_one_ticket",
    "build_ingestion_config",
    # Stable schemas
    "TicketInput",
    "PipelineDocument",
    "PreChunkDocument",
    "ChunkRecord",
    "TriageArtifact",
]
