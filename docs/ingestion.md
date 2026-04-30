# Ingestion

Canonical ingestion code lives in `src/vs_app/modules/ingestion`.
Importable batch-job code lives in `src/vs_app/jobs/jira_batch`.
Root-level operator wrappers live in `jobs/`.

Primary service surface:
- `IngestionService`
- `IngestTicketCommand`
- `IngestTicketResult`

The ingestion API route calls the service facade only. Route handlers do not perform Jira, file extraction, or LLM work directly.

Canonical job entrypoints:
- `py -3 jobs/ingest_tickets.py IDMT-19761 IDMT-8199 --output-dir ticket_data --build-faiss --force`
- `py -3 jobs/extract_tickets.py IDMT-19761 IDMT-8199 --output-dir jira_extraction`
- `py -3 jobs/update_faiss_index.py --input-dir ticket_data --index-dir ticket_data/_faiss`

Use `ingest_tickets.py` for historical RAG data. It writes:
- `ticket_data/<ticket-id>/summary.json`
- `ticket_data/_all_summaries.json`

Pass `--force` to overwrite an existing per-ticket `summary.json`.
Pass `--aggregate-name <name>.json` if you want the aggregate file to be named something other than `_all_summaries.json`.

Use `extract_tickets.py` only when you want raw extraction JSON for inspection.

The FAISS update job expects summary artifacts in `ticket_data`:
- `ticket_data/_all_summaries.json`, or
- `ticket_data/<ticket-id>/summary.json`

It writes the historical RAG index under `ticket_data/_faiss` by default.
