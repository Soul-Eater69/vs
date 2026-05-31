# Ingestion Cleanup Plan

This document is the execution plan for cleaning the ingestion side of the repo.

Repo-wide agent and coding-style rules live in `CLAUDE.md`.

## Branch strategy

Use a staging branch for ingestion cleanup:

```text
main
  stable branch

cleanup/ingestion-staging
  integration branch for ingestion cleanup

claude/*
  implementation branches for individual cleanup tasks
```

All ingestion cleanup PRs should target:

```text
base: cleanup/ingestion-staging
head: claude/<feature-name>
```

Only after the ingestion work is stable should we open:

```text
base: main
head: cleanup/ingestion-staging
```

## Why we are doing this

The current repo has useful code, but ingestion logic is spread across scripts, RAG modules, summary helpers, Jira helpers, and evaluation builders.

That makes it difficult to reason about:

```text
where Jira data is fetched
where attachment text is extracted
where summaries are generated
where ground truth is extracted
where Azure index documents are shaped
where evaluation datasets are built
```

The cleanup goal is to make ingestion one clear, testable flow.

## Business domain recap

```text
Value Stream = Jira Theme / GROUP issue
Stage = Jira Epic
Epic title contains the stage name
```

Prediction input should use only the original IDMT packet:

```text
summary
description
idea_card_text
attachment_text
extracted_text
generated_summary
```

Ground truth comes from Jira artifacts:

```text
Value Stream GT = linked Theme / GROUP issues
Stage GT = Epics under each Theme / GROUP
```

Never leak current-ticket Epic titles into prediction.

## Target ingestion structure

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

## Final ingestion pipeline

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

## Feature roadmap

### Feature 1: Canonical IDMT index document builder

Status: in progress in PR #1.

Purpose:

```text
Create the single source of truth for Azure-ready IDMT document shape.
```

Expected files:

```text
src/vs_app/ingestion/index_documents/__init__.py
src/vs_app/ingestion/index_documents/models.py
src/vs_app/ingestion/index_documents/idmt_document_builder.py
src/vs_app/ingestion/index_documents/azure_idmt_schema.py
tests/ingestion/test_idmt_document_builder.py
```

Responsibilities:

```text
TicketContext
ValueStreamSupport
StageSupport
build_indexed_idmt_document(...)
build_document_from_stage_dataset_row(...)
build_idmt_index_schema(...)
```

This feature should not change live ingestion, RAG, prediction, or evaluation behavior.

### Feature 2: Ingestion framework structure

Purpose:

```text
Create the clean folder structure under src/vs_app/ingestion and move ingestion-only code into it.
```

Responsibilities:

```text
create jira/, extraction/, summary/, ground_truth/, upload/, pipeline/ packages
move ingestion-only code gradually
keep compatibility wrappers if existing scripts import old paths
```

No behavior changes.

### Feature 3: Jira fetchers cleanup

Purpose:

```text
Isolate Jira API access from processing and prediction.
```

Target files:

```text
src/vs_app/ingestion/jira/ticket_fetcher.py
src/vs_app/ingestion/jira/attachment_fetcher.py
src/vs_app/ingestion/jira/issue_link_reader.py
src/vs_app/ingestion/jira/mapper.py
```

Responsibilities:

```text
fetch IDMT ticket
fetch attachments
read issue links
read Theme / GROUP issues
read child or linked Epics for GT
return raw or lightly mapped Jira objects
```

Should not summarize, classify, build Azure docs, or call prediction logic.

### Feature 4: Text extraction and consolidation cleanup

Purpose:

```text
Make original IDMT context extraction clean and reusable.
```

Target files:

```text
src/vs_app/ingestion/extraction/attachment_ranker.py
src/vs_app/ingestion/extraction/document_text_extractor.py
src/vs_app/ingestion/extraction/text_consolidator.py
```

Responsibilities:

```text
rank idea card/business attachments
prioritize PPT/PPTX, then PDF, then DOCX
extract document text
combine summary, description, attachment text, extracted text
preserve useful document structure
```

### Feature 5: Summary generation cleanup

Purpose:

```text
Isolate LLM summary generation from Jira fetching and prediction.
```

Target files:

```text
src/vs_app/ingestion/summary/prompt_builder.py
src/vs_app/ingestion/summary/summarizer.py
src/vs_app/ingestion/summary/mapper.py
```

Responsibilities:

```text
condense long idea card / attachment text
produce generated_summary
produce structured summary fields
keep summary prompts separate from prediction prompts
```

### Feature 6: Value Stream ground truth and support

Purpose:

```text
Cleanly extract and validate historic Value Stream ground truth.
```

Target files:

```text
src/vs_app/ingestion/ground_truth/value_stream_ground_truth.py
src/vs_app/ingestion/ground_truth/value_stream_support.py
```

Responsibilities:

```text
extract linked Theme / GROUP issues
match to approved 50 Value Streams
handle invalid VS values
use fuzzy match and LLM confirmation only when needed
classify support as direct / implied / weak_broad / not_in_context / unknown
```

### Feature 7: Stage ground truth extraction

Purpose:

```text
Cleanly extract stage GT from Jira Epics under each Theme / GROUP.
```

Target file:

```text
src/vs_app/ingestion/ground_truth/stage_ground_truth.py
```

Responsibilities:

```text
for each Theme / GROUP, find child or linked Epics
extract stage names from Epic titles
canonicalize against approved stages for that Value Stream
build gt_by_value_stream
```

Important rule:

```text
Theme/GROUP summary = Value Stream
Epic title = Stage
```

### Feature 8: Stage support classification

Purpose:

```text
Classify whether original ticket context supports each GT stage.
```

Target file:

```text
src/vs_app/ingestion/ground_truth/stage_support.py
```

Support types:

```text
direct
implied
weak_broad
not_in_context
unknown
```

Responsibilities:

```text
for each GT stage, inspect original ticket context
store evidence and reason
avoid hallucinated support
keep broad downstream BA choices as weak_broad or not_in_context
```

### Feature 9: Azure upload cleanup

Purpose:

```text
Make Azure Search upload code clean and isolated.
```

Target files:

```text
src/vs_app/ingestion/upload/azure_search_client.py
src/vs_app/ingestion/upload/index_manager.py
src/vs_app/ingestion/upload/uploader.py
```

Responsibilities:

```text
create/update idp_idmt_data_v2 schema
upload canonical IDMT documents
batch upload with status logging
avoid business logic in upload code
```

### Feature 10: Historic stage retrieval for prediction

Purpose:

```text
Use historic IDMT stage patterns as evidence for future stage prediction.
```

This comes after ingestion is clean.

Responsibilities:

```text
search idp_idmt_data_v2 for similar historic tickets
exclude current ticket during evaluation
for selected Value Stream, collect historic stages from same VS
pass compact historic stage evidence into stage prediction prompt
compare baseline vs historic-stage-assisted prediction
```

This should not be mixed into ingestion cleanup PRs until the ingestion foundation is stable.

## Current PR status convention

Each PR should include:

```text
summary
files changed
features implemented
tests run
explicit out-of-scope list
```

## Validation

Before reporting completion, run:

```powershell
uv run python -m compileall src scripts
uv run pytest
```

For focused PRs, also run focused tests.

## Quality checklist

Before merging any ingestion PR, verify:

```text
No unrelated files changed
No prediction/eval/RAG behavior changed unless intended
No useless wrappers or vague manager classes
No duplicate business logic
Existing scripts still compile
Tests pass
Branch targets cleanup/ingestion-staging, not main
```
