# Ingestion

Canonical ingestion code lives in `src/vs_app/modules/ingestion`.

Primary service surface:
- `IngestionService`
- `IngestTicketCommand`
- `IngestTicketResult`

The ingestion API route calls the service facade only. Route handlers do not perform Jira, Neo4j, file extraction, or LLM work directly.

Legacy paths under `ingestion.application.*` remain as compatibility shims.
