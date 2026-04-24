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

Optional local runtime data:
  idea_cards/
  mappings.json
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

## Optional local runtime data

The canonical FastAPI app can serve local idea-card browser data for the frontend.

Optional repo-root runtime data:

```text
idea_cards/
mappings.json
```

`GET /api/idea-cards` reads files from `idea_cards/`.
`GET /api/mappings` reads `mappings.json`.

These files may be absent in a clean clone. If absent:
- `/api/idea-cards` returns an empty card list.
- `/api/mappings` returns 404.

This does not break the historical RAG endpoint. Users can still call:

```text
POST /rag/value-streams
```

with `idea_card_text` or a real `ticket_id`.

Canonical run commands:

```bash
uvicorn vs_app.main:app --reload --port 8000
python -m vs_app.jobs.jira_batch.jobs.batch_ingest_job
python -m vs_app.jobs.jira_batch.jobs.extract_tickets
```

Neo4j currently acts only as an alternate ticket source. No product-impact knowledge graph is implemented in this repo.
