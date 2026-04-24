# Architecture

Final root tree:

```text
VS/
  docs/
  frontend/
  legacy/
  prompt_yaml/
  src/
  tests/
  .gitignore
  pyproject.toml
```

Canonical app code lives under `src/vs_app/`.

`src/vs_app/api` is the HTTP layer only.

`src/vs_app/modules` contains business workflows:
- `ingestion` for ticket ingestion
- `rag` for historical RAG / value-stream prediction
- `tickets` for shared ticket-domain logic

`src/vs_app/integrations` contains external-system adapters such as Jira, Neo4j ticket source access, file extraction, and other infrastructure-facing code.

`src/vs_app/shared` is reserved for low-level shared utilities.

`prompt_yaml/` remains at repo root intentionally as a runtime prompt resource folder.

`legacy/` contains quarantined old code and should not be imported by runtime code.

Canonical run commands:

```bash
uvicorn vs_app.main:app --reload --port 8000
python -m vs_app.jobs.jira_batch.jobs.batch_ingest_job
python -m vs_app.jobs.jira_batch.jobs.extract_tickets
```

Neo4j currently acts only as an alternate ticket source. No product-impact knowledge graph is implemented in this repo.
