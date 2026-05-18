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

## Historical Ticket Fetch Count

Stable baseline mode leaves `RAG_HISTORICAL_TICKET_FETCH_K` unset, which sets
runtime `historical_ticket_fetch_k` to 60.

For a top-6 experiment, set only this override before running eval:

PowerShell:
```powershell
$env:RAG_HISTORICAL_TICKET_FETCH_K="6"
```

CMD:
```bat
set RAG_HISTORICAL_TICKET_FETCH_K=6
```

Bash:
```bash
export RAG_HISTORICAL_TICKET_FETCH_K=6
```
