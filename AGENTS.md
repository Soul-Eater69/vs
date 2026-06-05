# AGENTS.md — Development handover

Guide for engineers/agents building the Theme & Epic Generation backend. Read this
first, then `CLAUDE.md` (coding rules) and `docs/TDD_GAP_AND_TASKS.md` (what to build).

## What this system does

Automates the SME/Business-Architect workflow: read an IDMT Engagement Request
(idea card + attachments), recommend approved **Value Streams**, then (after human
approval) generate the **Theme package** per Value Stream — Theme description,
selected **Stages**, **Business Needs**, and **L2/L3 capabilities**. SME stays the
final approver; Jira is the system of record. Source of truth: `tdd_updated_final.docx`
(extracted to `out/tdd_extracted.md`).

Two modules:
- **Ingestion** — turn historical IDMT tickets + Sightline catalogues into a trusted
  corpus: Cosmos (lineage + ground truth + catalogues) and `idp_idmt_data` (the single
  Azure AI Search index, entity types separated by `entityType`/metadata).
- **Data science / generation** — from a ticket id: condense → Value Stream
  recommendation (HITL approve) → Theme generation.

## Repository map (production package boundaries)

```
src/vs_app/
  sources/jira/            external extraction (idea-card-first attachment selection)   [partial]
  data_ingestion/{idmt,themes}/  batch transform + persistence to Cosmos/Search          [skeleton]
  storage/{cosmos,search}/ persistence repositories/adapters                             [skeleton]
  value_stream_generation/ runtime VS contract (wraps RAG)                               [done]
  stage_generation/        runtime stage selection (summary-only + foundational)         [done]
  theme_generation/        runtime Theme/Epic gen + service.py facade                    [done, flat outputs]
  modules/rag/             retrieval lanes, candidate merge/buckets, review pool         [mostly done]
  modules/stages/, modules/prompts/, modules/value_streams/  shared catalogues/prompts
  integrations/            low-level clients (Jira, Azure, LLM, files)
  domain/, validation/     shared models + validators                                    [skeleton]
prompt_yaml/               one prompt per file (no inline JSON schemas)
tests/                     fake-only unit tests (no live Azure/Jira/LLM)
```

Status legend matches `docs/TDD_GAP_AND_TASKS.md`.

## Non-negotiable rules (already enforced; keep them)

- **Stage prediction is summary-only**: only `generated_summary` + value stream +
  approved stages reach the stage LLM. Never the raw idea-card body, description,
  theme text, capabilities, historic stages, epic titles, or ground truth.
- **Idea-card-first**: prefer the idea-card attachment as the sole authoritative input;
  fall back to top-N attachments only when absent. (TDD: PPT/PPTX → PDF → DOC, top 4.)
- **No historic stage context** is passed to any generation prompt.
- **Foundational stages** for configured Value Streams are deterministic post-processing,
  never sent to the LLM.
- **Canonicalization**: `normalize_value_stream_key` is the dedup/merge/lookup key;
  it is intentionally distinct from `normalize_vs_name` and `reranker._norm`.
- **Prompts**: one prompt per `prompt_yaml/*.yaml`; reference the schema, do **not** inline
  JSON/field lists; structure is enforced by Pydantic output schemas.
- **Dependency injection everywhere**: every external dependency (LLM, RAG service,
  Jira client, summarizer, catalogue) is injectable so units are fake-tested.
- **Deterministic title** = IDMT ticket title + " - " + Value Stream name (no LLM).

## Coding & design conventions

- Clean production Python: small functions, explicit data shapes, dataclasses for core
  records, `to_dict()` for public contracts, lenient generators (warnings, never raise).
- Public per-entity output keys follow the agreed style: `*_id`, `*_name`, `rationale`,
  `confidence_score` (0–100 int, scaled from internal 0–1), `support_type`
  (`direct`/`implied`/``""``). Do not expose `rejected_stages`.
- Avoid generic `utils`/`manager`/`service` dumping grounds and one-line wrappers.
- See `CLAUDE.md` for the full style guide and naming preferences.

## How to validate (run before every PR)

```bash
uv run python -m compileall -q src scripts
PYTHONPATH=. uv run --extra dev pytest --continue-on-collection-errors
```
`uv` is at `/opt/homebrew/bin/uv`. Tests must not make live Azure/Jira/LLM calls.

## Branch & PR workflow

- Integration branch for this effort: **`feature/tdd-alignment`** (off `cleanup/ingestion-staging`).
- One feature per branch: `feature/<area>-<short-name>` → PR into `feature/tdd-alignment`.
- Each PR = one validated, code-reviewed feature: focused scope, fake-only tests,
  full suite green, honest PR summary. Do **not** target `main` directly.
- Do not run `--upload`/`--run`, create/recreate indexes, or call live services from CI/tests.

## Backend integration intent

The generation pipeline is the engine behind a backend API. Design for it:
- `theme_generation.service` is the single API-facing facade.
- Keep request/response as explicit contracts (dataclasses + `to_dict()`), all deps injected.
- The Theme-generation orchestration is **approval-gated** (HITL) and **async/parallel**
  (stage prediction ∥ description; then Business Needs / L2 / L3 fan out off stage output).

## Where to start
`docs/TDD_GAP_AND_TASKS.md` — TDD-vs-repo gap analysis and the epic/sub-task backlog.

## Pending actions (resume here)

- **Create the Linear tickets** for the 25-item backlog. Source: `docs/linear_tickets.csv`
  (import-ready) / `docs/TDD_GAP_AND_TASKS.md`. The `linear-server` MCP is added +
  connected; its tools load only on a fresh Claude session start. To create them:
  read the CSV, confirm the target Linear **team** and whether the 4 epics (A/B/C/D)
  should be **parent issues** with the tasks as **sub-issues** (vs. flat issues with
  epic labels), create ONE ticket as a dry run, then the rest — report each URL.
- Design source of truth: `docs/tdd_extracted.md` (text of `tdd_updated_final.docx`).
- After the Linear tickets exist, start implementation in dependency order
  (A6+A4+A5 schema/catalogues and B1 condense step first). One feature PR per task.
