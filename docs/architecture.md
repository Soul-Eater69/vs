# Architecture

`src/vs_app/api` is the HTTP layer only.

`src/vs_app/modules` contains business workflows:
- `ingestion` for ticket ingestion
- `rag` for historical RAG / value-stream prediction
- `tickets` for shared ticket-domain logic

`src/vs_app/integrations` contains external-system adapters such as Jira, Neo4j ticket source access, file extraction, and other infrastructure-facing code.

`src/vs_app/shared` is reserved for low-level shared utilities.

Legacy `ingestion/` and `pipelines/` paths are compatibility shims and old entrypoints. They remain in place so existing imports keep working while canonical code lives under `src/vs_app/`.

Neo4j currently acts only as an alternate ticket source. No product-impact knowledge graph is implemented in this repo.
