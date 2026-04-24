# API

FastAPI app entrypoint:
- `src/vs_app/main.py`

Run locally with:
- `uvicorn vs_app.main:app --reload --port 8000`

Current public routes:
- `GET /health`
- `POST /ingestion/tickets/{ticket_id}`
- `POST /rag/value-streams`

The API layer is intentionally thin:
- validate request data
- build command dataclasses
- call canonical services
- return response schemas

The API layer does not directly perform Jira calls, Neo4j calls, Azure search calls, or LLM calls inside route handlers.

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
