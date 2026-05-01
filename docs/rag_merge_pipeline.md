# RAG Merge Pipeline

This document explains the current value-stream RAG pipeline, with extra detail on the merge and finalization stages. It is written for debugging the UI tabs after retrieval has already happened.

The most important files are:

- `src/vs_app/api/routes/rag.py`: API and streaming route.
- `src/vs_app/api/schemas/rag_requests.py`: request options, including source-ticket exclusion.
- `src/vs_app/modules/rag/service.py`: service wrapper used by the non-streaming API.
- `src/vs_app/modules/rag/pipeline.py`: non-streaming pipeline orchestration.
- `src/vs_app/modules/rag/retrieval/semantic_retriever.py`: value-stream semantic retrieval.
- `src/vs_app/modules/rag/retrieval/historical_retriever.py`: historical FAISS retrieval and support aggregation.
- `src/vs_app/modules/rag/augmentation/candidate_merger.py`: merge, lane assignment, ranking, auto-select, and LLM candidate cap logic.
- `src/vs_app/modules/rag/augmentation/prompt_context.py`: prompt construction for direct and historical passes.
- `src/vs_app/modules/rag/augmentation/finalizer.py`: LLM execution, selected-value cleanup, evidence gates, and rescue passes.
- `src/vs_app/modules/rag/ranking/reranker.py`: LLM output sanitizer.
- `prompt_yaml/selection.yaml`: normal direct value-stream selection prompt.
- `prompt_yaml/historical_gap_selection.yaml`: historical gap-fill prompt.

## One-Line Summary

The pipeline retrieves candidates from two sources, converts historical FAISS ticket hits into value-stream support, merges semantic and historical evidence by value-stream name, protects useful lanes for the LLM, then finalizes with evidence gates so historical recall can recover misses without letting weak historical noise dominate.

## Request Shape

The UI sends a `ValueStreamRagRequest` to either:

- `POST /rag/value-streams/stream`
- `POST /rag/value-streams`

Important request fields:

- `ticket_id`: the Jira/idea-card ID, for example `IDMT-19761`.
- `idea_card_text`: raw uploaded text if the UI uploaded a file or extracted text.
- `top_k_historical`: how many historical neighbors the user wants.
- `top_k_value_streams`: how many value-stream semantic candidates the user wants.
- `use_llm_finalizer`: whether final selection should call the LLM.
- `exclude_source_ticket_from_historical`: leave-one-out toggle. Defaults to enabled in the current workflow.

When the user uploads text but has selected a ticket in the UI, the ticket ID still matters. The uploaded text is the query, but the selected ticket ID is used for source-ticket exclusion and ground-truth comparison.

## Pipeline Order

The streaming route in `src/vs_app/api/routes/rag.py` emits these steps:

1. `extract`: get raw idea-card text from `idea_card_text` or from the selected `ticket_id`.
2. `prepare`: clean and condense the idea-card text, while semantic value-stream search runs in parallel.
3. `historical`: search historical FAISS for similar prior tickets.
4. `merge`: merge semantic value-stream candidates with historical support.
5. `llm_select`: run direct and historical LLM selection passes.
6. `finalize`: assemble selected streams, debug payloads, ground truth, and evidence tabs.

The non-streaming path in `src/vs_app/modules/rag/pipeline.py` does the same core work.

## Retrieval Inputs

The pipeline prepares two versions of the query:

- `cleaned_query`: produced by `clean_ppt_text`; used for retrieval.
- `query_for_prompt`: produced by `condense_idea_card`; used for historical search and LLM prompts.

The candidate window is normalized:

```text
top_k = min(max(12, fetch_count), 50)
max_llm_candidates = min(max(top_k + 20, 40), 60)
historical max_ticket_hits = min(max(12, fetch_count), 40)
```

This means:

- The pipeline never asks for fewer than 12 retrieval candidates.
- It never retrieves more than 50 semantic value-stream candidates.
- It sends a wider 40-60 merged-candidate review window to the LLM so cross-confirmed historical matches are not cut just because the ticket has many labels.
- Historical FAISS ticket hits are capped at 40.

## Historical Source-Ticket Exclusion

The UI toggle is labeled `Exclude source ticket`.

When enabled, `_source_ticket_exclusions()` in `routes/rag.py` returns the current `ticket_id`. That ID is passed into `retrieve_historical_support()` as `exclude_ticket_ids`.

The historical retriever then:

1. Normalizes IDs with `_normalize_ticket_id()`.
2. Searches more than the requested number of tickets when exclusions are active.
3. Drops excluded ticket hits.
4. Stops after `max_ticket_hits` non-excluded results.
5. Runs a second filter pass with `filter_historical_result()` before merge.

The extra search depth is:

```text
exclusion_backfill = max(8, len(excluded_ticket_ids) * 3)
fetch_k = max_ticket_hits + exclusion_backfill
```

This matters because if `IDMT-19761` is the best FAISS neighbor for `IDMT-19761`, removing it can pull the whole historical lane down. Backfill helps, but it cannot fully replace a perfect self-hit.

The response includes:

```text
historical_excluded_ticket_ids
```

Use that field to verify the toggle actually reached the backend.

## Historical FAISS Hit Shape

Each historical ticket hit carries metadata from the summaries index:

- `ticket_id`
- `best_score`
- `title`
- `summary_preview`
- `value_stream_labels`
- `value_stream_names`
- `value_stream_ids`
- `stream_support_type`
- `direct_vs_names`
- `implied_vs_names`
- `label_source`
- `direct_functions_canonical`
- `implied_functions_canonical`

The important fields for merge are `direct_vs_names`, `implied_vs_names`, `label_source`, and `best_score`.

## From Ticket Hits To Value-Stream Support

`historical_retriever._build_support_from_faiss_hits()` converts ticket-level FAISS hits into value-stream-level support rows.

For every ticket hit, `_extract_hit_value_stream_support()` decides which value streams the ticket supports:

1. Prefer `direct_vs_names`.
2. Prefer `implied_vs_names` after removing anything already direct.
3. If direct/implied names are missing, use `stream_support_type`.
4. If support type is also missing, fall back to `value_stream_names` or `value_stream_labels`.

Fallback inference depends on the label source:

- `jira_issuelinks` fallback becomes `direct`.
- Other fallback labels become `implied`.

Each ticket has a total support weight of `1.0`, split across every stream on that ticket:

```text
per_ticket_weight = 1.0 / number_of_streams_on_ticket
```

This is important. A broad historical ticket tagged with 10 streams gives each stream only `0.1` weighted support. That prevents broad tickets from dominating merge just because they mention many value streams.

Each value-stream support row contains:

- `entity_id`
- `entity_name`
- `support_count`: number of ticket-stream support observations.
- `direct_count`: count of direct observations.
- `implied_count`: count of implied observations.
- `weighted_support_count`: sum of per-ticket weights.
- `weighted_direct_count`: weighted direct support.
- `weighted_implied_count`: weighted implied support.
- `best_support_score`: strongest FAISS score among supporting tickets.
- `avg_support_score`: average FAISS score across support observations.
- `supporting_ticket_ids`: unique supporting ticket IDs.
- `label_sources`: sources such as `jira_issuelinks` or `jira_themes_fallback`.
- `historical_reasons`: short summaries used later in prompts and reasons.

## Merge Inputs

`merge_candidate_sources()` receives:

- `semantic_candidates`: value streams found directly from the current idea card.
- `historical_support`: value streams inferred from similar prior tickets.
- `max_llm_candidates`: usually between 40 and 60.

Semantic candidates usually have:

- `entity_id`
- `entity_name`
- `description`
- `semantic_score`

Historical candidates have the support fields listed above.

## Merge Key

Merge is by normalized value-stream name:

```text
normalize = trim + lowercase + collapse whitespace
```

So `Issue Payment` and ` issue   payment ` merge into one row. Different names do not merge even if they are semantically related.

## Merge Construction

The merger first seeds a dictionary from semantic rows. Every seeded row starts as:

```text
from_semantic = true
from_historical = false
bucket = semantic_only
support_count = 0
ranking_score = 0
candidate_lane = unassigned
candidate_status = unclassified
```

Then historical rows are overlaid by normalized name.

If a historical row matches an existing semantic row:

```text
from_semantic = true
from_historical = true
bucket later becomes semantic_plus_historical
```

If a historical row does not match any semantic row, a new row is created:

```text
from_semantic = false
from_historical = true
bucket later becomes historical_only
semantic_score = 0
```

The historical fields replace the empty defaults on that merged row.

## Buckets

After all rows are merged, each row gets a `bucket`:

- `semantic_plus_historical`: semantic retrieval and historical retrieval both found the same value stream.
- `semantic_only`: only semantic retrieval found it.
- `historical_only`: only historical FAISS support found it.

In the UI:

- `MERGED` usually means `semantic_plus_historical`.
- `SEMANTIC ONLY` means `semantic_only`.
- `HISTORICAL ONLY` means `historical_only`.

## Historical Strength

Every row gets `historical_strength`, even if it has no historical support.

Formula:

```text
historical_strength =
  best_support_score
  + 0.18 * weighted_direct_count
  + 0.06 * weighted_implied_count
  + label_source_adjustment
```

Label source adjustment:

```text
jira_issuelinks present       => +0.06
only jira_themes_fallback     => -0.04
otherwise                    =>  0.00
```

Meaning:

- Strong FAISS similarity helps.
- Direct support helps three times more than implied support.
- Jira issue-link evidence gets a small boost.
- Theme fallback evidence gets a small penalty when it is the only source.

## Ranking Score

Every row gets `ranking_score`.

Formula:

```text
semantic_plus_historical:
  semantic_score + 0.25 * historical_strength

semantic_only:
  semantic_score

historical_only:
  0.70 * historical_strength
```

This deliberately keeps direct semantic evidence strong, but lets historical support move confirmed candidates up and lets historical-only candidates compete when repeated prior-ticket evidence is good.

## Candidate Lanes

Every row gets a `candidate_lane`:

- `confirmed_direct`: both semantic and historical found the stream.
- `historical_recall`: historical-only stream.
- `semantic_direct`: semantic-only stream.
- `weak_noise`: fallback if neither source exists.

These lanes control the LLM candidate cap. They also control which prompt gets used later.

## Candidate Statuses

Every row eventually gets a `candidate_status`:

- `auto_selected`: selected before the LLM because evidence is very strong.
- `sent_to_llm`: included in one of the LLM passes.
- `dropped_before_llm`: did not meet evidence gates or lost the LLM cap.
- `unclassified`: temporary state before the merge loop finishes.

The UI status pills are usually just these fields made visible.

## Auto-Select Rules

Auto-selection means the candidate bypasses the LLM and goes straight into final selected value streams.

### Auto-Select Confirmed Merge

`_should_auto_include_merged()` applies only to `semantic_plus_historical` rows.

It requires:

```text
from_semantic = true
from_historical = true
semantic_score >= 1.50
best_support_score >= 0.70
support_count >= 4
```

If it passes:

```text
candidate_status = auto_selected
candidate_status_reason = cross_confirmed_semantic_and_historical
```

### Auto-Select Historical-Only Direct Consensus

`_should_auto_include()` applies only to historical-only rows. The direct tier requires:

```text
from_semantic = false
direct_count >= 4
support_count >= 6
best_support_score >= 0.78
avg_support_score >= 0.65
weighted_support_count >= 2.0
```

If it passes:

```text
candidate_status = auto_selected
candidate_status_reason = strong_historical_support
```

### Auto-Select Historical-Only Heavy Implied Consensus

The implied tier requires:

```text
from_semantic = false
support_count >= 8
best_support_score >= 0.75
avg_support_score >= 0.65
weighted_support_count >= 2.5
```

This is for streams that repeatedly appear as implied downstream work across similar historical tickets.

## LLM Admission Gates

Rows not auto-selected may still be sent to the LLM.

### Confirmed Direct Lane

All `confirmed_direct` rows that were not auto-selected go into the confirmed-direct LLM pool. The lane quota decides how many survive.

### Semantic Direct Lane

`_should_send_semantic_to_llm()` requires:

```text
semantic_score >= 0.95
```

### Historical Recall Lane

`_should_send_historical_recall_to_llm()` is the most important gate for the misses in the screenshots.

It admits historical-only candidates when any of these conditions pass:

```text
direct_count >= 2
best_support_score >= 0.55
```

or:

```text
support_count >= 3
best_support_score >= 0.70
avg_support_score >= 0.58
```

or:

```text
implied_count >= 5
best_support_score >= 0.72
avg_support_score >= 0.60
```

or the leave-one-out moderate repeated support gate:

```text
support_count >= 5
best_support_score >= 0.60
avg_support_score >= 0.55
(direct_count >= 1 or implied_count >= 5)
```

or the broader repeated support gate:

```text
support_count >= 8
best_support_score >= 0.60
avg_support_score >= 0.52
```

or the weighted fallback:

```text
support_count >= 2
weighted_support_count >= max(1.0, support_count * 0.4)
```

or the last fallback:

```text
best_support_score >= 0.45
weighted_support_count >= 0.5
```

If none of these pass:

```text
candidate_status = dropped_before_llm
candidate_status_reason = insufficient_support
```

That is what a `DROPPED BEFORE LLM` pill means: the LLM never had a chance to choose it.

## LLM Lane Quotas

After admission, `_select_llm_candidates()` protects each lane with quotas.

For `max_llm_candidates = total`:

```text
confirmed_direct quota = min(max(1, ceil(total * 0.30)), 12)
historical_recall quota = min(max(1, ceil(total * 0.40)), 16)
semantic_direct quota = remaining
```

If `total >= 3`, the merger tries to keep at least one semantic candidate.

With a cap of `36`, the initial quotas are usually:

```text
confirmed_direct: 11
historical_recall: 15
semantic_direct: 10
```

Rows selected by lane quota get:

```text
candidate_status = sent_to_llm
candidate_status_reason = protected_confirmed_lane
```

or:

```text
candidate_status_reason = protected_historical_lane
```

or:

```text
candidate_status_reason = protected_semantic_lane
```

If quota space remains, overflow candidates from any lane can be added with:

```text
candidate_status_reason = within_llm_candidate_cap
```

If a row passed admission but lost the cap:

```text
candidate_status = dropped_before_llm
candidate_status_reason = llm_candidate_cap
```

So there are two very different kinds of "dropped":

- `insufficient_support`: evidence gate failed.
- `llm_candidate_cap`: evidence gate passed, but the prompt was full.

## Lane Priority

Within each lane, rows are sorted before the cap is applied.

Historical recall priority:

```text
unique supporting ticket count
support_count
direct_count
best_support_score
avg_support_score
weighted_support_count
historical_strength
ranking_score
```

Confirmed direct priority:

```text
ranking_score
semantic_score
support_count
best_support_score
```

Semantic direct priority:

```text
semantic_score
ranking_score
```

This means historical recall prefers repeated evidence across tickets before raw score.

## LLM Prompt Split

`finalizer.generate_value_streams()` receives:

- auto-selected rows from merge.
- LLM candidate rows from merge.
- historical FAISS ticket hits.

It splits LLM candidates with `_split_llm_candidates()`:

- Historical-only or `historical_recall` rows go to the historical gap pass.
- Everything else goes to the direct pass.

If both lists are non-empty, both LLM calls run in parallel.

## Direct LLM Pass

The direct pass uses:

- `build_direct_candidate_prompt()`
- `prompt_yaml/selection.yaml`
- `build_system_prompt(min_select=4, max_select=_direct_selection_max(len(candidates)))`

This pass is for candidates that are direct semantic matches or confirmed by both semantic and historical evidence.

`_direct_selection_max()` scales from 12 up to 24 as the candidate list grows. This matters for tickets whose ground truth contains more than 12 value streams; otherwise the LLM prompt itself prevents full recall even when retrieval and merge found the right streams.

The LLM is asked to select from the provided candidate list. The sanitizer later prevents it from inventing value streams that were not in the candidate list.

## Historical Gap LLM Pass

The historical gap pass uses:

- `build_historical_gap_prompt()`
- `prompt_yaml/historical_gap_selection.yaml`
- `build_system_prompt(min_select=0, max_select=12)`

This pass is allowed to select zero. It is supposed to be conservative because historical-only candidates can be genuine downstream misses or just weak analog noise.

The prompt includes:

- the idea-card query,
- historical-only value-stream candidates,
- supporting ticket IDs,
- support counts,
- direct/implied counts,
- historical reasons,
- FAISS hit context.

## LLM Output Sanitization

`sanitize_selected()` in `ranking/reranker.py` verifies that selected rows match the candidates that were actually sent to the LLM.

This protects the pipeline from:

- hallucinated value-stream names,
- renamed candidates,
- extra value streams not in the prompt,
- malformed structured output.

After sanitization, only valid candidate selections continue.

## Finalization

`_finalize_selected()` combines:

1. auto-selected rows,
2. filtered LLM selected rows,
3. rescued confirmed-merged rows,
4. rescued historical gap-fill rows.

It also records historical selections that the LLM picked but the evidence gate rejected.

The relevant budgets are:

```text
_CONFIRMED_MERGED_RESCUE_BUDGET = 12
_HISTORICAL_GAP_FILL_BUDGET = 4
_HISTORICAL_LLM_KEEP_CONFIDENCE = 0.70
```

## Historical LLM Selection Filter

Even if the historical gap LLM selects a historical-only candidate, `_finalize_selected()` checks evidence again.

A historical-only LLM selection is dropped if:

```text
candidate is historical_gap_fill
and not _passes_gap_fill_evidence(candidate)
and not _passes_high_confidence_historical_selection(selection, candidate)
```

High-confidence LLM override:

```text
LLM confidence >= 0.70
```

If it is dropped, it appears in:

```text
dropped_historical_gap_fill_value_streams
```

with:

```text
drop_reason = weak_historical_gap_fill_evidence
```

## Confirmed Merged Rescue

Confirmed merged rescue is for candidates that were sent to the direct LLM pass but the LLM failed to select, even though semantic and historical evidence jointly look strong.

`_passes_confirmed_merged_evidence()` requires:

```text
weighted_support_count >= 0.75
```

Then one of:

```text
support_count >= 5
semantic_score >= 1.20
best_support_score >= 0.60
```

or:

```text
support_count >= 5
semantic_score >= 1.00
```

or:

```text
support_count >= 3
semantic_score >= 1.35
best_support_score >= 0.65
```

Rescued rows get:

```text
selection_source = confirmed_merged_rescue
```

This rescue exists because the LLM can under-select obvious cross-confirmed candidates when the candidate list is crowded.

## Historical Gap-Fill Rescue

Historical gap-fill rescue is for historical-only candidates that were sent to the historical gap LLM pass but not selected, while evidence still looks coherent enough.

`_passes_gap_fill_evidence()` passes if any of these patterns are true.

Single-ticket dense direct evidence:

```text
ticket_count == 1
direct_count >= 4
support_count >= 4
best_support_score >= 0.62
avg_support_score >= 0.56
weighted_support_count >= 0.75
```

Moderate repeated direct evidence:

```text
support_count >= 5
direct_count >= 3
best_support_score >= 0.62
avg_support_score >= 0.56
weighted_support_count >= 0.60
```

Moderate repeated implied evidence:

```text
support_count >= 8
implied_count >= 5
best_support_score >= 0.60
avg_support_score >= 0.55
weighted_support_count >= 0.60
```

Strong multi-ticket evidence:

```text
support_count >= 3
ticket_count >= 2
best_support_score >= 0.70
avg_support_score >= 0.58
weighted_support_count >= 0.75
(direct_count >= 1 or implied_count >= 3)
```

Lower multi-ticket repeated evidence:

```text
support_count >= 5
ticket_count >= 2
best_support_score >= 0.64
avg_support_score >= 0.56
weighted_support_count >= 0.75
(direct_count >= 1 or implied_count >= 3)
```

Rescued rows get:

```text
selection_source = historical_gap_fill_rescue
```

Only up to four historical gap-fill rows are allowed in the final output, including the ones the LLM selected.

## Final Dedupe

Selected rows are deduped by lowercased `entity_name`.

If the same stream appears from auto-select and LLM selection:

- confidence becomes the max confidence,
- reasons are joined,
- supporting ticket IDs are merged,
- supporting chunk IDs are merged,
- support lists are truncated to five IDs.

## UI Tabs And What They Mean

### Selection

Final selected value streams after auto-select, LLM selection, rescues, and dedupe.

If a stream is missing here, it was either:

- never retrieved,
- retrieved but dropped before LLM,
- sent to LLM and not selected,
- selected by LLM but filtered by evidence,
- not rescued because the rescue gate failed or budget was used.

### Comparison

Compares final selected streams with ground truth from FAISS docs for the selected ticket.

Precision, recall, and F1 are based on exact value-stream name matching. If ground truth has stale or incomplete labels, this tab will make the model look worse than it actually is.

### LLM Passes

Shows direct and historical LLM output.

Use this to answer:

- Did the LLM see the candidate?
- Did it choose it?
- Was the choice later dropped?

### VS Candidates

Semantic retrieval candidates only.

If a value stream is here but not in final output, the issue is after semantic retrieval.

### Historic

Historical value-stream support aggregated from FAISS ticket hits.

If a value stream is here but marked `DROPPED BEFORE LLM`, inspect:

- `support_count`
- `direct_count`
- `implied_count`
- `best_support_score`
- `avg_support_score`
- `weighted_support_count`
- `candidate_status_reason`

### FAISS Hits

Raw historical ticket neighbors.

This answers whether FAISS found relevant prior tickets at all. If a needed value stream is absent here, the merge stage cannot recover it.

### Merged

The most important debugging tab.

It shows the combined candidate rows after semantic and historical evidence are joined. Use this tab to identify:

- `MERGED`: semantic and historical agree.
- `SEMANTIC ONLY`: direct retrieval found it, historical did not.
- `HISTORICAL ONLY`: historical found it, semantic did not.
- `SENT TO LLM`: candidate was in a prompt.
- `DROPPED BEFORE LLM`: candidate never reached a prompt.
- `PROTECTED CONFIRMED LANE`: candidate got confirmed lane quota.
- `PROTECTED HISTORIC LANE`: candidate got historical lane quota.
- `INSIDE LLM CAP`: candidate got overflow quota.

## Why Excluding The Same Ticket Can Lower Recall

When testing `IDMT-19761` against the historical FAISS index, the exact same ticket is often the strongest neighbor. If that self-hit is allowed, the historical lane can see the ground-truth labels directly. That inflates recall and hides whether the pipeline generalizes.

When `Exclude source ticket` is on:

- the self-hit is removed,
- `best_support_score` can drop,
- `avg_support_score` can drop,
- `support_count` for some ground-truth streams can drop,
- some historical-only streams may fall below LLM admission,
- some candidates may still reach the LLM but fail final evidence rescue.

That is expected for leave-one-out testing. The current tuning tries to compensate by:

- fetching backfill FAISS hits after exclusion,
- allowing repeated moderate evidence into the historical LLM lane,
- allowing moderate confirmed-merged rescue,
- allowing moderate historical gap-fill rescue.

The tradeoff is precision. Relaxing these gates can recover misses, but it can also admit streams like broad provider, payment, compliance, or engagement workflows when the analog tickets are only loosely related.

## How To Debug A Miss

Use this order.

1. Check `FAISS Hits`.

If the supporting tickets are absent, retrieval did not find enough historical evidence. Merge cannot fix this.

2. Check `Historic`.

If the stream is absent, FAISS found tickets but their metadata did not map to that value stream. Check direct/implied labels in the FAISS metadata.

3. Check `Merged`.

If the stream is `DROPPED BEFORE LLM`, inspect `candidate_status_reason`.

4. If `insufficient_support`, compare its support values to the historical recall gates.

The usual blockers are low `avg_support_score`, low `weighted_support_count`, or all evidence coming from broad implied fallback.

5. If `llm_candidate_cap`, the evidence passed but the prompt was full.

Increase the cap carefully or adjust lane quotas/priority.

6. If `SENT TO LLM`, check `LLM Passes`.

If the LLM rejected it, decide whether the prompt needs better evidence text or whether the finalizer should rescue that evidence pattern.

7. If the LLM selected it but it is missing from `Selection`, check dropped historical gap fill.

It likely failed `_passes_gap_fill_evidence()` and had LLM confidence below `0.70`.

## How To Debug A False Positive

Use this order.

1. Check whether the stream was `auto_selected`, `llm_selected`, or rescued.
2. If auto-selected, tighten the relevant auto-select gate.
3. If LLM-selected historical-only, inspect whether high-confidence LLM override allowed it.
4. If rescued, inspect the matching rescue gate.
5. Check `label_sources`. If the evidence is only `jira_themes_fallback`, consider adding a stronger penalty or requiring more tickets.
6. Check whether one broad ticket is supplying many streams. If so, weighted support is the intended control.

## Field Glossary

`semantic_score`
: Similarity score from value-stream semantic retrieval.

`best_support_score`
: Highest FAISS score among historical tickets supporting the value stream.

`avg_support_score`
: Average FAISS score across historical support observations for the value stream.

`support_count`
: Count of historical support observations.

`direct_count`
: Count of support observations where the historical ticket directly mapped to the value stream.

`implied_count`
: Count of support observations where the historical ticket implied the value stream.

`weighted_support_count`
: Total per-ticket support after splitting each ticket's weight across all streams on that ticket.

`weighted_direct_count`
: Weighted support from direct observations.

`weighted_implied_count`
: Weighted support from implied observations.

`historical_strength`
: Merge score derived from FAISS score, weighted direct/implied support, and label-source adjustment.

`ranking_score`
: Final pre-LLM ranking score used to sort candidates.

`bucket`
: Whether the row is semantic-only, historical-only, or merged.

`candidate_lane`
: Which protected lane the row belongs to before LLM candidate selection.

`candidate_status`
: Whether the row was auto-selected, sent to LLM, or dropped before LLM.

`candidate_status_reason`
: The specific reason for that status.

`supporting_ticket_ids`
: Historical tickets that supplied evidence for this value stream.

`historical_reasons`
: Short ticket summaries and function hints passed to prompts and final reasons.

## Practical Reading Of The Screenshots

When you see a historical-only row such as `Manage Invoice and Payment Receipt` with:

```text
HISTORICAL ONLY
DROPPED BEFORE LLM
INSUFFICIENT SUPPORT
RANK 0.482
HIST STRENGTH 0.689
HISTORIC 0.672
11 HITS
```

the key point is not just `11 HITS`. The gate also cares about direct count, implied count, average score, weighted support, and whether the support is broad. Many hits can still fail if they are weak, broad, implied, or concentrated in the wrong way.

When you see:

```text
HISTORICAL ONLY
SENT TO LLM
PROTECTED HISTORIC LANE
```

the merge stage did its job. If it is still missing from final selection, the question moves to the historical LLM pass and finalizer evidence gates.

When you see:

```text
MERGED
SENT TO LLM
PROTECTED CONFIRMED LANE
```

semantic and historical agreed, but the evidence did not hit the stricter auto-select threshold. If the LLM skips it, confirmed-merged rescue may still add it if the evidence gate passes.

## Where To Tune

Tune retrieval if:

- `FAISS Hits` does not contain useful neighbors.
- Source-ticket exclusion removes too much and backfill is not enough.

Tune historical support aggregation if:

- FAISS hits are useful but `Historic` does not show the right streams.
- Direct/implied metadata is wrong.
- Broad tickets overpower narrow tickets.

Tune merge if:

- useful historical-only candidates are `DROPPED BEFORE LLM`.
- useful candidates lose the LLM cap.
- auto-select is too aggressive or too conservative.

Tune prompts if:

- candidates are `SENT TO LLM`, evidence is good, but the LLM consistently rejects them.

Tune finalizer if:

- the LLM selects weak historical-only false positives.
- the LLM misses obvious confirmed-merged candidates.
- historical gap-fill rescue is too loose or too strict.

## Mental Model

The merge is not a simple concatenation. It is a triage layer:

1. Combine duplicate value streams from semantic and historical sources.
2. Score historical evidence separately from semantic evidence.
3. Give cross-confirmed candidates the safest lane.
4. Give historical-only candidates a protected but evidence-gated lane.
5. Prevent semantic-only candidates from crowding out historical recall.
6. Keep the LLM prompt bounded.
7. Let finalizer rescue strong missed candidates but cap historical gap-fill risk.

That is why a candidate can look close in the UI but still be missing from final output. It has to survive retrieval, support aggregation, merge gates, lane quotas, LLM selection, evidence filtering, rescue budgets, and final dedupe.
