# Architecture

Final root tree:

```text
VS/
  docs/
  frontend/
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

`src/vs_app/integrations` contains external-system adapters such as Jira, file extraction, and other infrastructure-facing code.

`src/vs_app/shared` is reserved for low-level shared utilities.

`prompt_yaml/` remains at repo root intentionally as a runtime prompt resource folder.

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
python -m vs_app.jobs.jira_batch.jobs.extract_tickets
```

Jira is the active ticket source. Older Neo4j and chunk-ingestion surfaces have been removed; they are not part of the runtime.

## Production package boundaries

Phase 1 of the production restructure introduces first-class top-level packages
under `src/vs_app/` that mark the intended boundaries:

- `sources/` — external extraction (e.g. Jira).
- `data_ingestion/` — batch/offline storage and indexing into Cosmos / Azure AI Search.
- `value_stream_generation/` — runtime Value Stream generation.
- `stage_generation/` — runtime stage selection.
- `theme_generation/` — runtime Theme/Epic field generation.
- `storage/` — persistence adapters (Cosmos, search).
- `integrations/` — low-level external clients.

Runtime Theme-generation code now lives at `vs_app.theme_generation`
(`retrieval`, `descriptions`, `orchestrator`, `search_adapter`). The previous
location `vs_app.ingestion.theme_generation` remains as thin compatibility shims
that re-export from the new path, so existing imports keep working. Ingestion,
index-document building, upload, ground-truth, extraction, and persistence code
remain under `vs_app.ingestion`.

The `data_ingestion`, `sources`, `value_stream_generation`, `stage_generation`,
`storage`, `domain`, and `validation` packages are docstring-only skeletons in
Phase 1 — boundaries are declared but not yet wired.
