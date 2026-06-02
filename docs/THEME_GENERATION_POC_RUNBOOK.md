# Theme-generation POC Runbook

## Purpose

Prove, locally and safely, that the Theme-generation POC data/index pipeline
works end to end on small fixture data — **with no Azure, no Jira, and no LLM
calls**. This is a dry-run proof of the export → upload flow, not a production
ingestion or runtime theme-generation path.

## Pipeline pieces

```
build stage GT payload        scripts/build_stage_ground_truth.py   (offline Jira; manual)
  -> export Theme-gen JSONL   scripts/export_theme_generation_jsonl.py
  -> optional enrichment      --support-input / --summary-input (precomputed files)
  -> dry-run / upload JSONL   scripts/upload_theme_generation_index.py
```

Document model (one Azure index, two document types):

- `document_type = "idmt"` — one per ticket; the vector-search document.
- `document_type = "theme"` — one per GROUP/theme; readable prompt example,
  **vector-less by design**.
- Stable ids: `idmt::{ticket_id}`, `theme::{ticket_id}::{group_id}`.

## Fixture-based local run

Fixtures (2 IDMT tickets, 3 themes total) live in
`tests/fixtures/theme_generation/`:

- `sample_stage_ground_truth.json` — export input (GT payload).
- `sample_support_input.json` — value-stream + stage support (covers IDMT-1001 only).
- `sample_summary_input.json` — key_terms/stakeholders/systems (covers IDMT-1001 only).

IDMT-1002 is intentionally missing from the enrichment files to demonstrate the
blank/`[]` fallback.

### 1. Base export (no enrichment)

```bash
uv run python scripts/export_theme_generation_jsonl.py \
  --gt-input tests/fixtures/theme_generation/sample_stage_ground_truth.json \
  --out /tmp/theme_gen.jsonl
```

### 2. Enriched export (precomputed inputs)

```bash
uv run python scripts/export_theme_generation_jsonl.py \
  --gt-input      tests/fixtures/theme_generation/sample_stage_ground_truth.json \
  --support-input tests/fixtures/theme_generation/sample_support_input.json \
  --summary-input tests/fixtures/theme_generation/sample_summary_input.json \
  --out /tmp/theme_gen_enriched.jsonl
```

### 3. Upload dry-run (zero Azure calls)

```bash
uv run python scripts/upload_theme_generation_index.py \
  --jsonl /tmp/theme_gen.jsonl \
  --dry-run
```

## Expected counts

Base and enriched exports produce the **same** document counts (enrichment fills
fields, not documents):

```
total docs        = 5
idmt docs         = 2
theme docs        = 3
docs with vectors = 0
```

The upload dry-run reports `mode = dry-run`, `uploaded = False`,
`index action = none`, and the same 5 / 2 / 3 / 0 counts.

## Safety notes

- **Dry-run is the default.** Real Azure operations happen only with `--upload`.
- **No Azure, Jira, or LLM** is touched by the fixture flow.
- Export reads a **GT file**, never Jira; it does not fetch Jira at runtime.
- **Theme docs are vector-less.** Only IDMT docs are embedded, and only when
  `--embed-idmt` is passed explicitly.
- `theme_description` is intentionally **blank** in the POC (the theme model
  generates it later).
- value-stream `evidence` is intentionally **blank** until a VS classifier emits
  source evidence (the current classifier returns a reason only).
- `stage_id` is **blank** unless a support row already carries one (no stage
  catalog resolution in export).

## Optional manual real upload (requires Azure credentials)

> ⚠️ Manual / not covered by tests. Only run against the POC index.

```bash
# Create the index (explicit flag, real mode):
uv run python scripts/upload_theme_generation_index.py \
  --jsonl /tmp/theme_gen.jsonl --upload --create-index

# Upload with IDMT embeddings (theme docs stay vector-less):
uv run python scripts/upload_theme_generation_index.py \
  --jsonl /tmp/theme_gen.jsonl --upload --embed-idmt
```

Index recreate/delete is triple-gated and refuses anything but the configured
POC index:

```bash
THEME_GEN_ALLOW_RECREATE=1 \
uv run python scripts/upload_theme_generation_index.py \
  --jsonl /tmp/theme_gen.jsonl --upload --recreate-index
```

- Requires `--recreate-index` **and** `THEME_GEN_ALLOW_RECREATE=1` **and** the
  name guard (only `idp_theme_generation_poc` /
  `THEME_GENERATION_AZURE_SEARCH_INDEX_NAME`; never the value-stream, historical,
  or summary indexes).
- Embedding dimensions come from `EMBEDDING_DIMENSION` (deployed model), not a
  hardcoded value.

## Manual theme generation (retrieval → stage picks → theme text)

`scripts/run_theme_generation.py` runs the assembled Theme-generation POC for a
sample idea. **Default is dry-run** (plan only): it validates inputs, loads the
local stage catalog for reporting, and constructs **no** embedding/Azure/LLM
client and makes **no** network call. Live retrieval + embedding + LLM run only
with the explicit `--run` flag. This script does **no Jira, no upload, no index
create/delete, and no API/UI wiring**.

> Note: `--value-streams` is comma-separated, so a value-stream name that itself
> contains a comma cannot be passed as-is in this POC CLI.

### Dry-run (default; no Azure/embedding/LLM)

```bash
uv run python scripts/run_theme_generation.py \
  --idea "Update UM operations for prior authorization handling." \
  --value-streams "Manage Utilization Management Program"
```

Prints a JSON plan: resolved index name, idea length, selected value streams,
stage catalog path, allowed-stage counts per VS, `top_k_idmt`, `max_examples`,
`would_run`, and `skipped: true`.

### Live run (`--run`; uses real Azure read-search + embedding + LLM)

> ⚠️ Manual / not covered by tests. `--run` performs a live embedding call, a
> read-only Azure vector search against the POC index, and live LLM calls.

```bash
uv run python scripts/run_theme_generation.py \
  --idea-file idea.txt \
  --value-streams "Manage Utilization Management Program,Manage Leads and opportunities" \
  --stage-catalog data/value_stream_stage_map.json \
  --top-k-idmt 15 --max-examples 5 \
  --run --output /tmp/theme_result.json
```

Required environment for `--run`:

- `AZURE_SEARCH_ENDPOINT`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
- `THEME_GENERATION_AZURE_SEARCH_INDEX_NAME` (default `idp_theme_generation_poc`)
- `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` (must match the index's `content_vector` dims)
- the LLM gateway vars used by `GenerationService` / `IDPChatOpenAI`

`--run` is **read-only**: it searches the POC index and calls the LLM. It never
fetches Jira, never uploads, and never creates or deletes an index.

## Remaining gaps

- Real GT payload generation from Jira is **not** included here (offline,
  manual, needs Jira credentials).
- Real Azure upload is **not** validated by tests (manual step).
- Direct LLM enrichment inside export is **not** built (Feature 12 Phase 2);
  use precomputed `--support-input` / `--summary-input`.
- The manual `--run` theme-generation path is **not** validated by tests (it uses
  live Azure/embedding/LLM); only the dry-run and injected-dependency core are
  tested.
- No API/UI wiring (out of scope until separately approved).
