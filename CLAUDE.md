# CLAUDE.md

This file is the repo-level instruction guide for Claude/Codex/LLM agents working in this repository.

## Project goal

We are automating Jira Theme and Epic generation from IDMT tickets.

The system reads an IDMT ticket, its description, Idea Card, and attachments, then predicts the correct business taxonomy outputs that a Business Architect would normally create manually in Jira.

Core domain mapping:

```text
Value Stream = Jira Theme / GROUP issue
Stage = Jira Epic
Epic title contains the stage name
```

Manual business flow:

```text
IDMT ticket / Idea Card / attachments
  -> Business Architect reads original business context
  -> Business Architect selects approved Value Stream(s)
  -> Each Value Stream becomes a Jira Theme / GROUP issue
  -> Business Architect chooses relevant stages under each selected Value Stream
  -> Each selected stage becomes a Jira Epic under that Theme
```

Prediction input must only use the original IDMT packet:

```text
IDMT summary
IDMT description
Idea Card text
attachment text
extracted raw text
generated summary when needed
```

Ground truth comes from Jira artifacts:

```text
Value Stream GT = linked Theme / GROUP issues
Stage GT = Epics under each Theme / GROUP
```

Never feed current-ticket Epic titles into prediction. Epic titles are answer-key artifacts.

## Current cleanup direction

The repo has useful logic, but ingestion is scattered across scripts, RAG modules, summary helpers, Jira helpers, and evaluation builders.

We are cleaning ingestion first.

Ingestion should become its own framework/layer under:

```text
src/vs_app/ingestion/
```

It should not be hidden inside RAG or evaluation scripts.

## Branch workflow

Do not target `main` directly for ingestion cleanup.

Use this workflow:

```text
main
  stable branch; only gets final cleanup merge

cleanup/ingestion-staging
  integration branch for ingestion cleanup work

claude/*
  feature branches created from cleanup/ingestion-staging
  merged back into cleanup/ingestion-staging
```

Future PRs should usually be:

```text
base: cleanup/ingestion-staging
head: claude/<feature-name>
```

Only after ingestion cleanup is stable should we open one final PR:

```text
base: main
head: cleanup/ingestion-staging
```

## PR scope rule

Prefer focused PRs, but batching related ingestion work is allowed when we need speed.

Never mix unrelated areas in the same PR.

For ingestion cleanup PRs, do not change:

```text
prediction behavior
evaluation behavior
RAG retrieval behavior
prompts
live Azure index uploads
model settings
```

unless the PR is explicitly about that feature.

## Coding style

Write clean production Python.

Preferred style:

```text
clear names
small meaningful functions
linear readable flow
explicit data shapes
simple dataclasses for core records
minimal nesting
helpers only when they have real responsibility
status logs for long-running jobs
single source of truth for important shapes/schemas
```

Avoid:

```text
useless one-line wrappers
vague Manager / Service / Processor classes
generic utility dumping grounds
over-engineered abstractions
deep nesting
duplicate business logic
random CLI flags
large functions doing multiple unrelated things
hidden business rules behind vague helpers
```

Bad:

```python
def process(data):
    return Manager().execute(data)
```

Good:

```python
def build_stage_pairs(gt_by_value_stream: dict[str, list[str]]) -> list[str]:
    pairs: list[str] = []
    for value_stream, stages in gt_by_value_stream.items():
        for stage in stages:
            pairs.append(f"{value_stream}::{stage}")
    return dedupe_text(pairs)
```

## Naming preference

Use domain names directly.

Prefer:

```text
ground_truth
value_stream_ground_truth
stage_ground_truth
stage_support
idmt_document_builder
```

Avoid vague names:

```text
labels
processor
manager
service
handler
engine
```

`ground_truth` is preferred over `labels` because we are extracting Jira answer-key artifacts.

## Ingestion target structure

Long-term target:

```text
src/vs_app/ingestion/
  __init__.py

  jira/
    __init__.py
    ticket_fetcher.py
    attachment_fetcher.py
    issue_link_reader.py
    mapper.py

  extraction/
    __init__.py
    attachment_ranker.py
    document_text_extractor.py
    text_consolidator.py

  summary/
    __init__.py
    prompt_builder.py
    summarizer.py
    mapper.py

  ground_truth/
    __init__.py
    value_stream_ground_truth.py
    value_stream_support.py
    stage_ground_truth.py
    stage_support.py

  index_documents/
    __init__.py
    models.py
    idmt_document_builder.py
    azure_idmt_schema.py

  upload/
    __init__.py
    azure_search_client.py
    index_manager.py
    uploader.py

  pipeline/
    __init__.py
    idmt_ingestion_pipeline.py
```

## Clean ingestion pipeline

The final ingestion flow should be:

```text
Jira fetchers
  -> Text extraction / consolidation
  -> Summary generation
  -> Value Stream ground truth extraction
  -> Value Stream support classification
  -> Stage ground truth extraction
  -> Stage support classification
  -> Canonical IDMT document builder
  -> Azure uploader
  -> Historic retrieval for VS/stage prediction
```

## Ground truth rules

Value Stream ground truth:

```text
Read linked Theme / GROUP issues from Jira.
Validate against the approved set of 50 Value Streams.
Use fuzzy matching and LLM confirmation only when needed.
```

Stage ground truth:

```text
For each Theme / GROUP, read Epics under that Theme.
A stage is represented by an Epic.
The Epic title contains the stage name.
Canonicalize against the approved stage catalog for that Value Stream.
```

Do not treat Theme summary as a stage.

## Support classification rules

Support classification explains whether the original ticket context supports a GT item.

Supported values:

```text
direct
implied
weak_broad
not_in_context
unknown
```

Use `unknown` when a GT value exists but support has not been classified yet.

Do not collapse `weak_broad` into `implied`; broad downstream BA choices should remain lower-confidence historic evidence.

## Azure index document rules

Canonical IDMT index documents should come from:

```text
src/vs_app/ingestion/index_documents/idmt_document_builder.py
```

This should remain the single source of truth for the indexed document shape.

Historic stage fields should include:

```text
gt_by_value_stream_json
stage_names
stage_ids
stage_pairs
stage_history_text
stage_support
stage_support_text
has_stage_gt
stage_count
```

Do not use current-ticket Epic titles as prediction input. These fields are for historic indexing, evaluation, and prompt evidence from previous tickets.

## Validation commands

Run these before reporting completion:

```powershell
uv run python -m compileall src scripts
uv run pytest
```

For focused PRs, also run the relevant focused test file.

## Reporting format

After changes, summarize:

```text
branch
files changed
features implemented
tests run
behavior intentionally not changed
remaining risks / next steps
```

Keep summaries honest and specific.
