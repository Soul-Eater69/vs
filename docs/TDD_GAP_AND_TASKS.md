# TDD Gap Analysis & Task Backlog

Source: `tdd_updated_final.docx` (extracted to `out/tdd_extracted.md`).
Baseline: `cleanup/ingestion-staging`. Working branch: `feature/tdd-alignment`.

Status: **Done** (matches TDD) · **Partial** (exists, needs alignment) · **Missing** (not built).
Each sub-task is a single feature PR: focused, fake-tested, code-reviewed, full suite green.

---

## Epic A — Ingestion & Unified Index (`idp_idmt_data` + Cosmos)

TDD §2–4. Builds the trusted corpus the data-science module reads.

| ID | Sub-task | TDD | Status | Notes |
|----|----------|-----|--------|-------|
| A1 | iTech DB eligibility audit (ER after 2023-01-01, Theme links, issue type, valid/partial VS GT) | §3 | Missing | Likely backend/infra-owned; define the contract + a fake-tested selector. |
| A2 | Jira fetch + enrichment (extraction, LLM summary, VS fuzzy match, direct/implied) | §2,§3 | Partial | Exists across `ingestion/jira`,`ground_truth`,`summary`; realign field names to TDD. |
| A3 | Cosmos **historic IDMT document** model + repository (top-level fields, `properties`, `properties.themes[]`) | §4.1–4.3 | Missing | `storage/cosmos` + `data_ingestion/idmt` are skeletons. |
| A4 | Cosmos **Value Stream catalogue** ingestion (Sightline → `valueStreamId/Name/Description`) | §2,§4.6–4.7 | Missing | We only have the 50-VS approved registry (names). |
| A5 | Cosmos **Stage / L2 / L3 catalogues** + `stage→L2` / `stage→L3` mappings | §2,§6.2 | Missing | Governs stage prediction + capabilities; nothing today. |
| A6 | `idp_idmt_data` **unified index schema** (`entityType`, `content`/`content_vector`, `properties`) — historical IDMT doc + valueStream doc | §4.4–4.7 | Partial | Current `index_documents` uses a different schema; realign to `entityType` model. |
| A7 | Attachment extraction priority (PPT/PPTX → PDF → DOC, top-4; `idea_card.ppt/pptx` detection) | §3,§5.2 | Partial | Idea-card-first done; add PPT-priority ordering + `.ppt/.pptx` naming. |
| A8 | Unified-index uploader/manager (dry-run-safe) for both entity types | §4 | Partial | Have theme-gen uploader; align to A6 schema. |

## Epic B — Value Stream Generation (Data Science)

TDD §5. From ticket id → ranked VS recommendation set (HITL gate after).

| ID | Sub-task | TDD | Status | Notes |
|----|----------|-----|--------|-------|
| B1 | **Condense step** — single LLM pass → `summaryFields` + `generationSignals` (evidence objects `{text,source,sourceSection}`, empty when absent, never invented) | §5.1 | Missing | **Core dependency** for VS + all theme calls. Biggest single gap. |
| B2 | Ticket context extraction (idea_card-first → normalized context object) | §5.2 | Partial | Builds on A7 + runtime ticket-id wiring; produce the normalized context. |
| B3 | Retrieval lanes against `idp_idmt_data` (VS catalogue lane top-50 `entityType=valueStream`; historical ER lane top-6 `entityType=EngagementRequest`, user-selectable) | §5.3 | Partial | Two-source retrieval exists; align to index schema + user-selected top-6. |
| B4 | Candidate merge / ranking / 3 buckets (Semantic+Historic, Historic-only, Semantic-only) | §5.4 | Done | `candidate_merger` lanes already match; minor naming alignment. |
| B5 | Review-pool LLM selection **split into two parallel calls** + merge/dedupe/validate | §5.5 | Partial | Single pass today; parallel split previously deferred (HIGH risk, needs eval). |
| B6 | VS selection output (`valueStreamId/Name`, confidence, bucket `direct/implied`, reason, source tickets, default top 10 + custom instruction) | §5.5 | Done | `value_stream_generation` contract mostly matches; add `valueStreamId` from catalogue (needs A4). |
| B7 | Quality metrics / eval harness (VS precision/recall, stage precision/recall, latency) | §5.6 | Partial | Eval tests exist; formalize the metric harness. |

## Epic C — Theme Generation (post-HITL)

TDD §6. One Theme package per approved Value Stream.

| ID | Sub-task | TDD | Status | Notes |
|----|----------|-----|--------|-------|
| C1 | **HITL approval gate** — no theme output until SME confirms the VS set | §6.1 | Missing | Composition currently runs straight through. |
| C2 | **Async/parallel orchestration** — stage prediction ∥ description; then Business Needs / L2 / L3 fan out off stage output | §6.1 | Missing | Composition is sequential today. |
| C3 | Stage prediction with **governed Cosmos stage list** + richer context (summaryFields + select generationSignals); output `stageId, rank, reason, evidence, validationStatus` | §6.2 | Partial | Summary-only done; add catalogue `stageId`, rank, evidence, validationStatus (needs A5/B1). |
| C4 | Theme Description **structured output** (`themeOverview`, `productAvailability?`, `initiativeOverview`, `keyFeatures`, `digitalExperience?`, `integrationOperationalCapabilities?`) | §6.2 | Partial | Flat string today → structured schema. |
| C5 | Business Needs **structured, per-stage** (`businessProductFeatures{featureName, numbered needs, notes, dependencies, businessRules}`, `operationalTraining?`, `operationalReporting?`, `validationStatus`) | §6.2 | Partial | Flat string today → per-stage structured. |
| C6 | L2 capabilities from **Cosmos stage→L2 mappings** (grouped by stage; name/description/reason) | §6.2 | Partial | Freeform today → governed (needs A5). |
| C7 | L3 capabilities from **Cosmos stage→L3 mappings** (parallel with L2, off stage output) | §6.2 | Partial | Freeform today → governed (needs A5). |
| C8 | Deterministic Theme title (IDMT title + VS name) | §6.1,§6.3 | Done | Implemented. |
| C9 | Final Theme package assembly + `validationStatus` + recommendation-until-approved | §6.3 | Partial | Assemble C3–C8 into the TDD package shape. |

## Epic D — Backend Integration & API (cross-cutting)

| ID | Sub-task | Status | Notes |
|----|----------|--------|-------|
| D1 | API contracts: submit ticket_id → VS recommendations; approve VS set; trigger theme generation; return theme package | Missing/design | `theme_generation.service` is the facade; design request/response contracts. |
| D2 | Async orchestration layer for the C2 fan-out + progress/streaming | Design | Maps to the parallel calls in §6.1. |
| D3 | Persist recommendations + SME feedback to Cosmos | Missing | Depends on A3. |

---

## Dependency order (suggested)

1. **A6 + A4 + A5** (unified index schema + catalogues) and **B1** (condense step) — everything downstream needs these.
2. **A3/A2/A7/A8** ingestion pipeline to populate the corpus.
3. **B2/B3** retrieval alignment; **B5** parallel review pool (gated on eval); **B6** add `valueStreamId`.
4. **C1/C2** approval gate + async orchestration; **C3–C7** governed + structured outputs; **C9** package.
5. **D1–D3** backend API + persistence (in parallel once contracts settle).

## Already done (do not rebuild)
B4 buckets · C8 title · summary-only stage rule · idea-card-first selection · foundational stages ·
canonicalization unification · runtime ticket_id wiring (injected client + summarizer) · the flat
end-to-end fake composition (Value Stream → Stage → Description → Business Needs → L2 → L3 → Title).
