# API

FastAPI app entrypoint:
- `src/vs_app/main.py`

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
