# Ingestion

Canonical ingestion code lives in `src/vs_app/modules/ingestion`.
Batch job code lives in `src/vs_app/jobs/jira_batch`.

Primary service surface:
- `IngestionService`
- `IngestTicketCommand`
- `IngestTicketResult`

The ingestion API route calls the service facade only. Route handlers do not perform Jira, Neo4j, file extraction, or LLM work directly.

Canonical job entrypoints:
- `python -m vs_app.jobs.jira_batch.jobs.batch_ingest_job`
- `python -m vs_app.jobs.jira_batch.jobs.extract_tickets`
