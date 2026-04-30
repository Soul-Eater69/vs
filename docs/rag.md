# RAG

Canonical RAG code lives in `src/vs_app/modules/rag`.

For the detailed merge and finalization flow, see [rag_merge_pipeline.md](rag_merge_pipeline.md).

Public API exposes historical RAG only:
- `POST /rag/value-streams`
- `POST /rag/value-streams/stream`

Historical RAG in this repo means:
1. Retrieve candidate value streams from the value-stream index.
2. Retrieve historical support from the historical/local index.
3. Merge both sources with the existing candidate merge logic.
4. Optionally run the existing finalizer.
5. Return value-stream prediction output.

This repo does not expose plain/semantic/combined public modes.
