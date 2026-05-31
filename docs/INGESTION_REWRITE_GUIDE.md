# Ingestion Rewrite Guide — Coding Style, Structure, and Phase 1 Scope

## Branch

Work branch:

```text
cleanup/ingestion-rewrite-guide
```

This branch is for cleaning the ingestion layer first. Do not mix this with stage-prediction prompt changes, evaluation experiments, or RAG behavior changes.

---

## What We Are Building

We are automating Jira Theme and Epic generation from IDMT tickets.

Critical domain mapping:

```text
Value Stream = Jira Theme / GROUP issue
Stage = Jira Epic
Epic title contains the stage name
```

Manual business flow:

```text
IDMT ticket / Idea Card / attachments
  ↓
BA reads original business context
  ↓
BA selects approved Value Stream(s)
  ↓
Each selected Value Stream becomes a Theme / GROUP issue
  ↓
BA chooses relevant stages under each selected Value Stream
  ↓
Each selected stage becomes an Epic under that Theme
```

Prediction input must be only the original packet:

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

Do not leak current-ticket Epic titles into prediction. Epic titles are answer-key artifacts.

---

# 1. Coding Style Rules

The cleanup should produce code that is easy to read and maintain. The repo currently has too much scattered code and too many one-off scripts. Fixing ingestion means creating one obvious spine, not adding more random helpers.

## 1.1 Preferred style

Use:

```text
small meaningful functions
clear names
explicit data shapes
linear readable flow
simple dataclasses for core objects
local helpers only when they carry real responsibility
status logs for long-running work
single source of truth for document shape/schema
```

Avoid:

```text
useless wrapper functions
vague Manager / Service / Processor classes
large generic utility files
hidden business logic behind generic helpers
deep nesting
duplicate data-shaping code
random CLI flags
rewriting unrelated modules in the same PR
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

## 1.2 Rewriting is allowed

You may rewrite code when the current implementation is messy, duplicated, or hard to follow.

But preserve behavior.

Allowed:

```text
rewrite messy functions into cleaner functions
move ingestion-only logic out of RAG modules
replace duplicate ad hoc data shaping with canonical builder
rename unclear functions when usage is local
add adapters so old scripts still work
```

Not allowed in this phase:

```text
breaking current evaluation scripts
removing working scripts without replacement
changing Value Stream or stage prediction behavior
changing GT extraction semantics
changing model prompts
changing live Azure indexes
```

---

# 2. Desired Ingestion Architecture

Ingestion should become a separate framework/layer. It should not be buried inside RAG.

Target direction:

```text
src/vs_app/ingestion/
  jira/
    ticket_fetcher.py
    attachment_fetcher.py
    issue_link_reader.py

  extraction/
    text_consolidator.py
    attachment_ranker.py

  summary/
    prompt_builder.py
    mapper.py
    summarizer.py

  labels/
    value_stream_gt.py
    value_stream_support.py
    stage_gt.py
    stage_support.py

  index_documents/
    __init__.py
    models.py
    idmt_document_builder.py
    azure_idmt_schema.py

  upload/
    azure_search_client.py
    index_manager.py
    uploader.py
```

Do not build all of this now. Phase 1 only creates the `index_documents` package and the canonical document shape.

---

# 3. Phase 1 Scope

Create:

```text
src/vs_app/ingestion/index_documents/
  __init__.py
  models.py
  idmt_document_builder.py
  azure_idmt_schema.py
```

Add tests:

```text
tests/ingestion/test_idmt_document_builder.py
```

This phase creates the canonical Azure-ready IDMT document shape.

Do not refactor prediction/evaluation yet.

Do not rewrite Jira fetching yet.

Do not rewrite attachment extraction yet.

Do not implement historic stage retrieval yet.

---

# 4. Current Dataset Shape To Support

Current stage dataset rows look like this:

```python
{
    "ticket_id": ticket_id,
    "idea_card": {
        "summary": "...",
        "description": "...",
        "idea_card_text": "...",
        "attachment_text": "...",
        "extracted_text": "...",
        "generated_summary": "..."
    },
    "ground_truth": {
        "gt_by_value_stream": {
            "Value Stream Name": ["Stage Name"]
        }
    },
    "warnings": []
}
```

Current ingestion/context fields:

```text
summary
description
idea_card_text
attachment_text
extracted_text
generated_summary
```

Current stage GT field:

```text
ground_truth.gt_by_value_stream
```

The new builder must convert this existing row shape without regenerating summaries.

---

# 5. `models.py`

Create simple dataclasses.

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TicketContext:
    ticket_id: str
    summary: str = ""
    description: str = ""
    idea_card_text: str = ""
    attachment_text: str = ""
    extracted_text: str = ""
    generated_summary: str = ""
    retrieval_text: str = ""


@dataclass
class ValueStreamSupport:
    value_stream_name: str
    value_stream_id: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""
    source: str = ""
    confidence: float | None = None


@dataclass
class StageSupport:
    value_stream_name: str
    stage_name: str
    value_stream_id: str = ""
    stage_id: str = ""
    support_type: str = ""
    reason: str = ""
    evidence: str = ""
    source: str = ""
    confidence: float | None = None
```

Allowed support types:

```text
direct
implied
weak_broad
not_in_context
unknown
```

Use `unknown` when the support has not been classified yet.

---

# 6. `idmt_document_builder.py`

Create:

```python
def build_indexed_idmt_document(
    *,
    ticket_context: TicketContext,
    value_stream_support: list[ValueStreamSupport],
    gt_by_value_stream: dict[str, list[str]],
    stage_support: list[StageSupport] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
```

## 6.1 Required output fields

```text
id
ticket_id
ticket_key

summary
description
idea_card_text
attachment_text
extracted_text
generated_summary
retrieval_text

valid_value_stream_names
valid_value_stream_ids
value_stream_pairs
value_stream_support
value_stream_support_text

gt_by_value_stream_json
stage_names
stage_ids
stage_pairs
stage_history_text
stage_support
stage_support_text

has_valid_value_streams
valid_value_stream_count
has_stage_gt
stage_count

source_system
index_version
ingested_at
warnings
```

## 6.2 Field rules

### ID fields

Use normalized ticket ID for:

```text
id
ticket_id
ticket_key
```

### retrieval_text

Use `ticket_context.retrieval_text` when present.

Otherwise build from:

```text
summary
generated_summary
description
extracted_text
```

Keep it clean and compact. Do not dump huge raw text if a summary exists.

### valid_value_stream_names

Build from:

```text
value_stream_support.value_stream_name
gt_by_value_stream keys
```

Clean and dedupe.

### valid_value_stream_ids

Build from:

```text
value_stream_support.value_stream_id
```

Clean and dedupe.

### value_stream_pairs

Format:

```text
value_stream_id::value_stream_name
```

If ID is missing:

```text
value_stream_name
```

### value_stream_support

List of dicts:

```json
{
  "value_stream_name": "Configure, Price, and Quote",
  "value_stream_id": "VS-CPQ",
  "support_type": "implied",
  "reason": "Ticket describes quoting work.",
  "evidence": "quote available for sales executive",
  "source": "description",
  "confidence": 0.82
}
```

Remove empty junk rows.

### value_stream_support_text

Flattened searchable/prompt text:

```text
VS Configure, Price, and Quote support implied evidence quote available for sales executive reason ticket describes quoting work
```

### gt_by_value_stream_json

Deterministic JSON string.

Rules:

```text
clean value stream names
clean stage names
dedupe stages
sort value stream keys
sort or deterministically order stages
```

Example:

```json
{"Configure, Price, and Quote":["Account Configuration","Generate Quote and Present to Customer"]}
```

### stage_names

Flat deduped list of all stages from `gt_by_value_stream`.

### stage_ids

From `stage_support.stage_id`.

Can be empty if stage IDs are not mapped yet.

### stage_pairs

Format:

```text
Value Stream Name::Stage Name
```

Example:

```text
Configure, Price, and Quote::Account Configuration
```

### stage_history_text

Compact text for historic prompt evidence:

```text
For Value Stream Configure, Price, and Quote, BA-created stages were Account Configuration and Generate Quote and Present to Customer. For Value Stream Establish Product Offering, BA-created stage was Prepare Product Offering.
```

### stage_support

If `stage_support` is provided, clean and use it.

If it is missing, build fallback rows from `gt_by_value_stream`:

```json
{
  "value_stream_name": "Configure, Price, and Quote",
  "value_stream_id": "",
  "stage_name": "Account Configuration",
  "stage_id": "",
  "support_type": "unknown",
  "reason": "",
  "evidence": "",
  "source": "jira_gt",
  "confidence": null
}
```

Reason:

```text
We know BA created this stage from Jira GT.
We may not yet know whether the original context directly or implicitly supports it.
```

### stage_support_text

Flattened searchable text:

```text
VS Configure, Price, and Quote stage Account Configuration support unknown source jira_gt
```

If reason/evidence exists, include it.

### counts

```text
has_valid_value_streams = bool(valid_value_stream_names)
valid_value_stream_count = len(valid_value_stream_names)
has_stage_gt = bool(stage_names)
stage_count = len(stage_names)
```

### metadata

Allow metadata override for:

```text
source_system
index_version
ingested_at
warnings
```

Defaults:

```text
source_system = jira
index_version = idmt-v2-stage-history
ingested_at = current UTC ISO timestamp
warnings = []
```

## 6.3 Suggested clean implementation layout

Use meaningful helpers only.

```python
def build_indexed_idmt_document(...):
    context = clean_ticket_context(ticket_context)
    gt_map = clean_gt_by_value_stream(gt_by_value_stream)
    vs_rows = clean_value_stream_support(value_stream_support)
    stage_rows = clean_stage_support(stage_support) or stage_support_from_gt(gt_map)

    valid_vs_names = build_value_stream_names(vs_rows, gt_map)
    stage_names = build_stage_names(gt_map)
    stage_pairs = build_stage_pairs(gt_map)

    return {...}
```

Useful helpers:

```text
clean_text
dedupe_text
utc_now
clean_gt_by_value_stream
stage_support_from_gt
build_stage_pairs
build_stage_history_text
build_value_stream_support_text
build_stage_support_text
```

Avoid excessive helper fragmentation.

---

# 7. Adapter From Existing Dataset Row

Add:

```python
def build_document_from_stage_dataset_row(
    ticket_id: str,
    row: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ...
```

Read:

```python
idea_card = row.get("idea_card") or {}
ground_truth = row.get("ground_truth") or {}
gt_by_value_stream = ground_truth.get("gt_by_value_stream") or {}
warnings = row.get("warnings") or []
```

Build `TicketContext`:

```python
TicketContext(
    ticket_id=ticket_id,
    summary=idea_card.get("summary", ""),
    description=idea_card.get("description", ""),
    idea_card_text=idea_card.get("idea_card_text", ""),
    attachment_text=idea_card.get("attachment_text", ""),
    extracted_text=idea_card.get("extracted_text", ""),
    generated_summary=idea_card.get("generated_summary", ""),
)
```

Build basic VS support from GT keys:

```python
ValueStreamSupport(
    value_stream_name=value_stream_name,
    support_type="unknown",
    source="jira_gt",
)
```

Then call `build_indexed_idmt_document`.

This lets already-generated dataset JSON be converted into Azure-ready documents without regenerating summaries.

---

# 8. `azure_idmt_schema.py`

Create:

```python
def build_idmt_index_schema(
    *,
    index_name: str = "idp_idmt_data_v2",
    embedding_dimensions: int = 3072,
    vector_profile_name: str = "idmt-vector-profile",
) -> dict[str, Any]:
    ...
```

Use 3072 because current config uses `text-embedding-3-large`.

Keep this as a plain dict. Do not introduce Azure SDK classes unless the repo already standardizes on them.

Schema groups:

```text
Identity:
  id
  ticket_id
  ticket_key

Text:
  summary
  description
  idea_card_text
  attachment_text
  extracted_text
  generated_summary
  retrieval_text

Vectors:
  summary_vector
  context_vector

Value Stream:
  valid_value_stream_names
  valid_value_stream_ids
  value_stream_pairs
  value_stream_support
  value_stream_support_text

Stage:
  gt_by_value_stream_json
  stage_names
  stage_ids
  stage_pairs
  stage_history_text
  stage_support
  stage_support_text
  has_stage_gt
  stage_count

Metadata:
  has_valid_value_streams
  valid_value_stream_count
  source_system
  index_version
  ingested_at
  warnings
```

Do not add separate stage vectors yet.

Use complex fields for:

```text
value_stream_support = Collection(Edm.ComplexType)
stage_support = Collection(Edm.ComplexType)
```

Also keep flattened text fields because they are easier for retrieval and prompt building.

---

# 9. Tests

Add:

```text
tests/ingestion/test_idmt_document_builder.py
```

Test cases:

## converts current dataset row

Input:

```python
row = {
    "idea_card": {
        "summary": "Add quoting support",
        "description": "Need quote available for sales executive",
        "generated_summary": "Quoting support and account setup",
    },
    "ground_truth": {
        "gt_by_value_stream": {
            "Configure, Price, and Quote": [
                "Generate Quote and Present to Customer",
                "Account Configuration",
            ]
        }
    },
    "warnings": ["sample warning"],
}
```

Assert:

```text
ticket_id == IDMT-1
valid_value_stream_names includes Configure, Price, and Quote
stage_names includes both stages
stage_pairs include VS::Stage pairs
has_stage_gt is true
stage_count == 2
```

## JSON roundtrip

Assert:

```python
json.loads(doc["gt_by_value_stream_json"]) == {
    "Configure, Price, and Quote": [
        "Account Configuration",
        "Generate Quote and Present to Customer",
    ]
}
```

## unknown stage support fallback

When no `stage_support` is passed:

```text
stage_support rows should exist
support_type should be unknown
source should be jira_gt
```

## empty GT

Assert:

```text
has_stage_gt is false
stage_count == 0
stage_names == []
stage_pairs == []
```

## dedupe and clean

Input duplicate VS/stage rows with extra spaces.

Assert lists are clean and deduped.

---

# 10. Do Not Do In This Phase

Do not:

```text
rewrite Jira client
rewrite attachment extraction
rewrite summary prompts
rewrite RAG retrieval
delete old scripts
change stage prediction behavior
change Value Stream evaluation behavior
implement historic stage retrieval yet
update live Azure index
add too many config flags
```

This is only the first cleanup step: canonical ingestion document shape.

---

# 11. Acceptance Criteria

Run:

```powershell
uv run python -m compileall src scripts
```

Run tests:

```powershell
uv run pytest tests/ingestion/test_idmt_document_builder.py
```

Expected:

```text
existing scripts still compile
new ingestion document builder tests pass
current stage dataset rows can convert to Azure-ready documents
no prediction/evaluation behavior changed
code is readable and not over-engineered
```

---

# 12. Next Phase After This

Only after this ingestion document builder is clean:

```text
1. Add stage support classification.
2. Add stage fields to historic IDMT ingestion upload.
3. Add historic stage retrieval for stage prediction.
4. Add predictable recall metric.
5. Compare baseline stage prediction vs historic-stage-assisted prediction.
```
