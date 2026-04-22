# Historical RAG Pipeline — Complete Logic Reference

## Overview

The `historic-rag` pipeline predicts which **value streams (VS)** an idea card belongs to by combining two independent retrieval signals:

1. **Semantic retrieval** — Azure AI Search finds VS whose entity descriptions are semantically similar to the card text.
2. **Historical retrieval** — FAISS finds past tickets similar to the card and votes in the VS those tickets were tagged with.

The two signals are merged, auto-selected winners bypass the LLM, borderline candidates go to an LLM judge, and the final list is deduplicated and returned.

---

## End-to-End Flow

```
Raw idea card text
        │
        ├─── clean_ppt_text() ──────────────────────► Azure AI Search (VS entity index)
        │                                                      │
        │                                              semantic_candidates[]
        │                                              (top-k VS by description match)
        │
        └─── condense_idea_card() ─────────────────► FAISS (historical ticket summaries)
                                                               │
                                                       ticket_hits[] → aggregate per-VS
                                                               │
                                                       historical_support[]
                                                       (VS with hit counts, scores, reasons)

                        │                                      │
                        └──────────── merge_candidate_sources() ───────────────┘
                                               │
                                    ┌──────────┴──────────┐
                                    │   merged candidates  │
                                    │  (bucket assigned,   │
                                    │   ranked by score)   │
                                    └──────────┬──────────┘
                                               │
                              ┌────────────────┼────────────────┐
                              │                │                │
                    auto_include_merged   auto_include      send_to_llm
                    (both signals,        (hist-only,       (everything else
                     very strong)         strong direct)     above threshold)
                              │                │                │
                              └────────────────┤                │
                                         auto_selected[]        │
                                                         llm_candidates[]
                                                                │
                                                    LLM (6–12 selections)
                                                                │
                                                      llm_selected[]
                                                                │
                                              merge auto + llm → final output
```

---

## Stage 1: Query Preparation (`pipeline.py`)

```python
cleaned_query    = clean_ppt_text(query)        # for Azure AI Search
query_for_prompt = condense_idea_card(query)    # for FAISS + LLM prompt
```

Two different queries are produced from the same raw card text:

| Query | Used for | Why different |
|-------|----------|---------------|
| `cleaned_query` | Azure AI Search VS retrieval | Raw text with PPT artifacts removed; Azure handles its own embeddings |
| `query_for_prompt` | FAISS ticket retrieval + LLM prompt | Condensed by GPT-4o-mini into structured summary matching the format historical tickets were indexed in — better embedding alignment |

---

## Stage 2: Semantic Retrieval (`retrieval.py → retrieve_semantic_candidates`)

```
cleaned_query
      │
      ▼
Azure AI Search (hybrid: BM25 + vector + semantic reranker)
filter: node_type == 'ValueStream'
top_k: up to 50 (controlled by fetch_count slider)
      │
      ▼
Returns VS entity records scored by description-to-card similarity
      │
      ▼
Dedup by entity_id → sort by semantic_score DESC
      │
      ▼
semantic_candidates[]  ← each has: entity_name, entity_id, description, semantic_score
```

**Score type:** Azure semantic reranker score, range roughly **0 – 3**. Plain vector fallback scores are **0 – 1** (only used if reranker is unavailable).

**What it finds well:** VS whose description text is semantically close to the card. E.g., a card about "pricing workflows" will score "Configure, Price, and Quote" highly.

**What it misses:** VS that are implied but use different vocabulary. E.g., a Women's Health card doesn't mention "member care" explicitly, so "Manage Member Care" scores low even if that VS owns those workflows.

---

## Stage 3: Historical Retrieval (`retrieval.py → retrieve_historical_support`)

```
query_for_prompt (condensed summary)
      │
      ▼
FAISS local index (ticket summaries, embedded)
top_k: up to 24 tickets
      │
      ▼
ticket_hits[] — each hit has:
  - ticket_id, title, best_score (cosine similarity)
  - summary_preview (first 320 chars)
  - direct_vs_names[]   ← VS this ticket is explicitly about
  - implied_vs_names[]  ← VS adjacent/downstream to this ticket
  - direct_functions_canonical[]
  - implied_functions_canonical[]
  - stream_support_type {vs_name: "direct"|"implied"}
  - label_source ("jira_issuelinks" | "jira_themes_fallback" | ...)
      │
      ▼
_build_support_from_faiss_hits()  ← aggregates per VS across all hits
      │
      ▼
historical_support[]  ← one entry per VS, contains:
  support_count, direct_count, implied_count
  weighted_support_count (diluted by VS count per ticket)
  best_support_score, avg_support_score
  historical_reasons[]  ← up to 3 labeled analog strings
```

### Per-ticket weight dilution

Each ticket's contribution to a VS is divided by how many VS that ticket is tagged with:

```
per_ticket_weight = 1.0 / len(all_vs_on_this_ticket)
```

A ticket tagged with 12 VS contributes only **0.083** per VS. This prevents a single multi-VS ticket from artificially inflating all 12 streams.

### Inference type resolution (priority order)

```
1. Use direct_vs_names / implied_vs_names (explicit per-name classification)
2. Fall back to stream_support_type dict {name: "direct"|"implied"}
3. Fall back to label_source:
   - "jira_issuelinks"    → treat all as "direct"
   - anything else        → treat all as "implied"
```

### historical_reasons format

Each VS gets up to 3 analog strings, formatted as:

```
[IDMT-19761 / direct] Overhaul existing Special Beginnings/Women's and Family Health... | functions: member_enrollment, benefit_design
[IDMT-31170 / implied] HCSC proposes CareWay+, an in-house ASO FI configurable...
```

Direct analogs are shown first in the LLM prompt.

---

## Stage 4: Merge & Augment (`augmentation.py → merge_candidate_sources`)

### 4a. Keyed merge by normalized name

```python
key = " ".join(name.strip().lower().split())  # e.g. "manage member care"
```

- Semantic candidates are loaded first (they have `description` and `semantic_score`)
- Historical entries are merged in: if the key already exists, historical fields are added onto the existing record; if not, a new `historical_only` record is created

### 4b. Bucket assignment

| Condition | Bucket |
|-----------|--------|
| Both semantic + historical | `semantic_plus_historical` |
| Semantic only | `semantic_only` |
| Historical only | `historical_only` |

### 4c. Historical strength score

Used as a secondary rank signal for historical-only candidates:

```
historical_strength =
    best_support_score
  + 0.18 × weighted_direct_count
  + 0.06 × weighted_implied_count
  + label_source_adjustment          # +0.06 for jira_issuelinks, -0.04 for themes_fallback
```

A VS with `best_support_score=0.755` and `weighted_direct_count=0.67` gets:
`0.755 + 0.18×0.67 = 0.876`

### 4d. Ranking score (blended sort)

**Before this fix:** semantic candidates always ranked above all historical-only candidates → position #29 in a 30-item list → cut by the 24-candidate cap before reaching the LLM.

**After fix:** blended score projects historical-only onto the same scale as semantic:

```
semantic_plus_historical  →  semantic_score + 0.25 × hist_strength
semantic_only             →  semantic_score
historical_only           →  0.70 × hist_strength
```

Example comparisons:

| Candidate | Bucket | Scores | Ranking score |
|-----------|--------|--------|---------------|
| Issue Payment | merged | sem=1.365, hist=0.876 | 1.365 + 0.25×0.876 = **1.584** |
| Manage Member Care | hist-only | hist=0.876 | 0.70×0.876 = **0.613** |
| Weak semantic VS | semantic | sem=0.50 | **0.500** |

Manage Member Care (0.613) now outranks a weak semantic candidate (0.500) and makes it into the 24-candidate window.

---

## Stage 5: Auto-selection Gate

Each merged candidate passes through three gates in order. The first gate that fires removes the candidate from the LLM queue.

```
For each candidate (in ranking order):
    │
    ├─ Gate 1: _should_auto_include_merged()  ──► auto_selected (merged path)
    │
    ├─ Gate 2: _should_auto_include()         ──► auto_selected (hist-only path)
    │
    ├─ Gate 3: from_semantic OR _should_send_to_llm()  ──► llm_candidates
    │
    └─ (else) dropped — not enough signal
```

### Gate 1: `_should_auto_include_merged` — Strong merged candidates

Fires only when **both** signals independently agree strongly. Bypasses the LLM entirely.

```
Conditions (ALL must pass):
  ✓ from_semantic AND from_historical        (both signals present)
  ✓ semantic_score >= 1.5                    (strong reranker — well above average)
  ✓ best_support_score >= 0.60              (strong historical similarity)
  ✓ support_count >= 5                       (at least 5 analog tickets)
```

**Why 1.5 semantic?** Azure reranker range is 0–3. Score of 1.5 means the VS description strongly matches the card — not just related, genuinely relevant. Threshold is above 1.0 to avoid semantically-adjacent VS (e.g. "Administer UMP" when the card is actually about "Manage UMP").

**Confidence formula:** `min(0.95, 0.55 + 0.10×min(semantic,2) + 0.08×min(weighted_support,3))`
Issue Payment example: `0.55 + 0.10×1.5 + 0.08×0.67 = 0.754`

### Gate 2: `_should_auto_include` — Strong historical-only candidates

Fires for `historical_only` candidates with strong direct evidence. Recovers VS that Azure AI Search missed entirely due to vocabulary gap.

```
Conditions:
  ✓ from_semantic == False                   (only for hist-only)
  ✓ best_support_score >= 0.60

  Then either:
    SHORTCUT: direct_count >= 3              ← bypasses weighted count check
              AND support_count >= 3
    OR STANDARD PATH:
              support_count >= 3
              weighted_support_count >= max(1.5, 3×0.45)
              weighted_direct_count >= weighted_implied_count
```

**Why the shortcut?** `weighted_support_count` is diluted by multi-VS tickets. A VS with 8 direct hits across tickets that each have 12 VS attached gives `weighted = 8 × (1/12) = 0.67` — well below the 1.5 threshold. But 8 direct-tagged analogs is strong signal regardless of dilution. The shortcut trusts raw `direct_count >= 3` instead.

**Why 3 direct, not 2?** Common VS (e.g. "Ensure Compliance", "Manage Enterprise Risk") appear as `direct` on many unrelated tickets in the corpus. Requiring 3 avoids auto-selecting them on flimsy pattern matches.

**Confidence formula:** `min(0.92, 0.38 + 0.12×min(weighted_support,4) + 0.10×min(best_score,1))`

### Gate 3: `_should_send_to_llm` — Moderate historical support

```
If support_count >= moderate_count (2):
    weighted_support >= max(1.0, 2×0.4)   →  send to LLM

Else:
    best_support_score >= 0.45
    AND weighted_support >= 0.5            →  send to LLM
```

All `from_semantic` candidates go to LLM unconditionally (the `or` short-circuits).

### Cap

```python
llm_candidates = llm_candidates[:max_llm_candidates]  # max_llm_candidates = 24
```

---

## Stage 6: LLM Selection (`generation.py`)

### Prompt construction

For each LLM candidate a block is built:

```
1. Manage Member Care
Entity ID: abc-123
Evidence bucket: historical_only
Historical support: 8 tickets (3 direct, 5 implied), best similarity 0.7553, average similarity 0.7200
Analog evidence:
  - [IDMT-19761 / direct] Overhaul existing Special Beginnings/Women's and Family Health...
  - [IDMT-22445 / direct] Transition member portal to unified digital experience...
  - [IDMT-31100 / implied] CareWay+ steerage product for Group Medical ASO FI...
```

Direct-tagged analogs are shown before implied ones so the LLM sees the strongest evidence first.

### System prompt rules (from `historical_rag_selection.yaml`)

```
Evidence buckets:
  semantic_plus_historical  → strongest; usually keep
  semantic_only             → judge by business fit
  historical_only           → read analog summaries to judge relevance

Analog evidence rules:
  [ticket / direct]  → explicitly tagged with this VS
  [ticket / implied] → inferred from functions/downstream relationships (weaker)
  Repeated direct analogs with relevant summaries = strong evidence
  Single implied analog = usually not enough

Decision policy:
  - Favor quality over quantity: prefer 8–10 well-supported over 12 marginal
  - Include historical-only when analog summaries show defensible business connection
  - Do NOT require exact wording match — vocabulary gap is expected
  - When "Manage X" and "Administer X" both appear, select only the one with
    stronger direct evidence, not both

Confidence guidance:
  0.85–1.0   strong direct fit or multiple direct analogs with similar summaries
  0.65–0.84  meaningful fit or strong historical recovery
  0.40–0.64  plausible but borderline
  <0.40      do not select

Selection count: min 6, max 12
```

### Output merge

```
final_selected = dedupe(auto_selected + llm_selected)
```

If the same VS appears in both (e.g. LLM also picked something auto-selected), the higher confidence wins and ticket IDs are merged (up to 5).

---

## Stage 7: Final Output (`pipeline.py`)

```python
{
  "selected_value_streams":          [...],  # auto + llm merged, final answer
  "auto_selected_value_streams":     [...],  # what bypassed the LLM
  "llm_selected_value_streams":      [...],  # what the LLM chose
  "semantic_candidate_value_streams":[...],  # raw Azure AI Search output
  "historical_candidate_value_streams":[...],# raw FAISS-aggregated support
  "merged_candidate_value_streams":  [...],  # after merge + bucket assignment
  "historical_ticket_hits":          [...],  # individual FAISS ticket hits
  "llm_candidates":                  [...],  # what the LLM actually saw
}
```

---

## Key Thresholds Reference

| Parameter | Value | Where | Purpose |
|-----------|-------|--------|---------|
| `top_k` semantic | 12–50 | `pipeline.py` | How many VS Azure returns |
| `max_ticket_hits` | 12–24 | `pipeline.py` | How many FAISS ticket hits |
| `max_llm_candidates` | 24 | `augmentation.py` | Cap before LLM |
| `strong_support_score` | 0.60 | `augmentation.py` | Min best_support_score for auto-include |
| `strong_support_count` | 3 | `augmentation.py` | Min tickets for auto-include (hist-only) |
| `moderate_support_count` | 2 | `augmentation.py` | Min tickets to send to LLM |
| `moderate_support_score` | 0.45 | `augmentation.py` | Min score to send to LLM |
| Auto-include merged: semantic threshold | **1.5** | `augmentation.py` | Strong reranker score |
| Auto-include merged: support_count | **5** | `augmentation.py` | Min tickets for merged auto-include |
| Auto-include hist-only: direct shortcut | **3** | `augmentation.py` | Raw direct_count to bypass weight check |
| LLM max_select | **12** | `generation.py` | Max VS the LLM can pick |
| LLM min_select | **6** | `generation.py` | Min VS the LLM must pick |
| Ranking: historical-only scale factor | **0.70** | `augmentation.py` | Projects hist score onto semantic scale |
| Ranking: merged historical boost | **0.25** | `augmentation.py` | Historical adds to semantic for merged |
| label_source bonus: jira_issuelinks | **+0.06** | `augmentation.py` | Explicit link = stronger signal |
| label_source penalty: themes_fallback | **-0.04** | `augmentation.py` | Theme-inferred = weaker signal |

---

## Why Azure AI Search Misses Implied VS

Azure AI Search scores VS entity **descriptions** against the card text using embedding similarity. The key constraint:

```
Card: "Women's Health platform consolidation — Maternity, Fertility, Menopause"
VS description of "Manage Member Care": "End-to-end member services, care navigation..."
```

These embed into different vector spaces. No overlap in surface vocabulary → low cosine similarity → VS not retrieved.

FAISS fixes this indirectly: it finds **historical tickets** that discussed Women's Health programs, and those tickets were also tagged with "Manage Member Care" because the member services workflow owns those programs. The historical signal recovers what semantic similarity misses.

---

## Precision vs Recall Tradeoff Log

| Config | Recall | Precision | Notes |
|--------|--------|-----------|-------|
| Before fixes | ~58% | ~66% | historical_only never reached LLM (cut at #29) |
| After sort fix + max_select=15 + loose thresholds | 100% | 52% | Too many auto-selected, LLM over-picked |
| **Current** | ~92%+ | ~65%+ | Tightened merged threshold (1.0→1.5, 4→5 hits), direct shortcut (2→3), max_select back to 12 |

---

## Files

| File | Role |
|------|------|
| `pipelines/historical_rag/pipeline.py` | Orchestrator — query prep, wires all stages |
| `pipelines/historical_rag/retrieval.py` | Azure AI Search + FAISS retrieval, support aggregation |
| `pipelines/historical_rag/augmentation.py` | Merge, rank, auto-select gate |
| `pipelines/historical_rag/generation.py` | LLM prompt builder, merge auto+llm output |
| `prompt_yaml/historical_rag_selection.yaml` | System + user prompt templates for LLM selection |
