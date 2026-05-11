"""Run batch evaluation for value-stream RAG over local idea-card files.

Example:
  py -3 scripts/evaluate_rag_batch.py --limit 100 --concurrency 4

The script expects local idea cards under ``idea_cards/`` by default. Ground
truth labels and historical RAG hits default to the Azure historical summary
index.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
import time
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


SUPPORTED_EXTENSIONS = {
    ".pptx",
    ".ppt",
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".markdown",
}

EVAL_LLM_ENV_DEFAULTS = {
    # Batch eval fans out many RAG calls, so default to the faster model/reasoning
    # profile unless the operator explicitly overrides these env vars.
    "CONDENSE_LLM_MODEL": "gpt-5-mini-idp",
    "CONDENSE_LLM_REASONING_EFFORT": "low",
    "GENERATION_LLM_MODEL": "gpt-5-mini-idp",
    "GENERATION_LLM_REASONING_EFFORT": "medium",
}


@dataclass(frozen=True)
class EvaluationItem:
    ticket_id: str
    path: Path
    ground_truth: list[str]


@dataclass
class TicketMetrics:
    ticket_id: str
    file_name: str
    status: str
    elapsed_seconds: float
    precision: float
    recall: float
    f1: float
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    predicted_count: int
    ground_truth_count: int
    predicted_value_streams: list[str]
    ground_truth_value_streams: list[str]
    true_positives: list[str]
    false_positives: list[str]
    false_negatives: list[str]
    selected_count: int
    llm_candidate_count: int
    merged_candidate_count: int
    historical_hit_count: int
    excluded_ticket_ids: list[str]
    output_count_mode: str = "fixed"
    final_output_count: int = 0
    gt_buffer: int = 0
    runtime_final_output_count: int = 0
    semantic_fetch_k: int = 0
    historical_ticket_fetch_k: int = 0
    llm_candidate_window: int = 0
    candidate_count: int = 0
    prompt_chars: int = 0
    final_llm_ms: int = 0
    total_ms: int = 0
    candidate_window_semantic_plus_historical: int = 0
    candidate_window_historical_only: int = 0
    candidate_window_semantic_only: int = 0
    selection_source_llm_pick: int = 0
    selection_source_safe_backfill: int = 0
    foundational_signals: list[str] | None = None
    foundational_signal_source: str = ""
    foundational_signal_count: int = 0
    foundational_candidate_count: int = 0
    selected_foundational_count: int = 0
    missed_foundational_candidates: list[str] | None = None
    foundational_recall: float | None = None
    error: str = ""


def main() -> int:
    args = parse_args()
    apply_eval_llm_defaults()

    idea_cards_dir = Path(args.idea_cards_dir)
    faiss_dir = Path(args.historical_faiss_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ground_truth_by_ticket = load_ground_truth(
        source=args.ground_truth_source,
        faiss_dir=faiss_dir,
        azure_index_name=args.historical_azure_index_name,
    )
    file_ticket_ids = load_ticket_ids_file(args.ticket_ids_file)
    combined_ticket_ids: list[str] | None = None
    if args.ticket_ids or file_ticket_ids:
        combined_ticket_ids = list(args.ticket_ids or []) + file_ticket_ids

    items = discover_items(
        idea_cards_dir=idea_cards_dir,
        ground_truth_by_ticket=ground_truth_by_ticket,
        limit=args.limit,
        seed=args.seed,
        shuffle=not args.no_shuffle,
        require_ground_truth=not args.allow_missing_ground_truth,
        ticket_ids=combined_ticket_ids,
        min_ground_truth_streams=args.min_ground_truth_streams,
    )

    if not items:
        raise SystemExit("No idea cards with usable ground truth were found.")

    if len(items) < args.warn_below:
        print(
            f"Warning: only {len(items)} cards found; requested around {args.warn_below}-{args.limit}.",
            file=sys.stderr,
        )

    sweep_counts = parse_sweep_counts(args.sweep_counts, args.max_output_count)

    started = datetime.now(timezone.utc)
    print(
        f"Evaluating {len(items)} tickets with concurrency={args.concurrency}, "
        f"top_k={args.fetch_count}, exclude_source={not args.include_source_ticket}, "
        f"min_truth_streams={args.min_ground_truth_streams}, "
        f"historical_backend={args.historical_search_backend}, "
        f"output_count_mode={args.output_count_mode}"
        + (f", sweep_counts={sweep_counts}" if args.output_count_mode == "sweep" else "")
    )

    results = run_batch(
        items,
        concurrency=args.concurrency,
        fetch_count=args.fetch_count,
        historical_faiss_dir=str(faiss_dir),
        historical_search_backend=args.historical_search_backend,
        historical_azure_index_name=args.historical_azure_index_name,
        exclude_source_ticket=not args.include_source_ticket,
        retries=args.retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        output_count_mode=args.output_count_mode,
        final_output_count=args.final_output_count,
        gt_buffer=args.gt_buffer,
        min_output_count=args.min_output_count,
        max_output_count=args.max_output_count,
        sweep_counts=sweep_counts,
    )

    summary = summarize(results)
    payload = {
        "created_at": started.isoformat(),
        "idea_cards_dir": str(idea_cards_dir),
        "historical_faiss_dir": str(faiss_dir),
        "historical_search_backend": args.historical_search_backend,
        "historical_azure_index_name": args.historical_azure_index_name,
        "ground_truth_source": args.ground_truth_source,
        "limit": args.limit,
        "concurrency": args.concurrency,
        "fetch_count": args.fetch_count,
        "retries": args.retries,
        "retry_backoff_seconds": args.retry_backoff_seconds,
        "min_ground_truth_streams": args.min_ground_truth_streams,
        "exclude_source_ticket": not args.include_source_ticket,
        "output_count_mode": args.output_count_mode,
        "final_output_count": args.final_output_count,
        "gt_buffer": args.gt_buffer,
        "min_output_count": args.min_output_count,
        "max_output_count": args.max_output_count,
        "sweep_counts": sweep_counts if args.output_count_mode == "sweep" else None,
        "summary": summary,
        "results": [serialize_result(row) for row in results],
    }

    json_path = output_dir / args.json_name
    jsonl_path = output_dir / args.jsonl_name
    csv_path = output_dir / args.csv_name
    write_json(json_path, payload)
    write_jsonl(jsonl_path, results)
    write_csv(csv_path, results)

    sweep_summary_path = None
    if args.output_count_mode == "sweep":
        sweep_summary_path = output_dir / "rag_sweep_summary.json"
        write_json(sweep_summary_path, summarize_by_output_count(results))

    print_summary(summary)
    print(f"Wrote JSON:  {json_path}")
    print(f"Wrote JSONL: {jsonl_path}")
    print(f"Wrote CSV:   {csv_path}")
    if sweep_summary_path is not None:
        print(f"Wrote sweep summary: {sweep_summary_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate value-stream RAG precision/recall across local idea cards.",
    )
    parser.add_argument("--idea-cards-dir", default="idea_cards")
    parser.add_argument("--historical-faiss-dir", default="ticket_data/_faiss")
    parser.add_argument(
        "--historical-search-backend",
        choices=["faiss", "azure"],
        default="azure",
        help="Backend for historical ticket hits during RAG evaluation.",
    )
    parser.add_argument(
        "--historical-azure-index-name",
        default=None,
        help="Azure AI Search historical summary index name. Defaults to HISTORICAL_AZURE_SEARCH_INDEX_NAME.",
    )
    parser.add_argument(
        "--ground-truth-source",
        choices=["azure", "faiss"],
        default="azure",
        help="Source for eval ground-truth labels.",
    )
    parser.add_argument("--output-dir", default="output/rag_eval")
    parser.add_argument("--json-name", default="rag_batch_eval.json")
    parser.add_argument(
        "--jsonl-name",
        default="rag_batch_eval.jsonl",
        help="Per-row JSONL output (one result per line). Easier to grep/jq than the bundled JSON.",
    )
    parser.add_argument("--csv-name", default="rag_batch_eval.csv")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--warn-below",
        type=int,
        default=75,
        help="Print a warning when fewer than this many cards are available.",
    )
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry each ticket this many times after transient gateway failures.",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=5.0,
        help="Initial per-ticket retry backoff for transient gateway failures.",
    )
    parser.add_argument("--fetch-count", type=int, default=30)
    parser.add_argument(
        "--min-ground-truth-streams",
        type=int,
        default=2,
        help=(
            "Minimum number of ground-truth value streams a ticket must have to be sampled. "
            "Default 2 avoids single-label tickets dominating precision/recall diagnostics."
        ),
    )
    parser.add_argument(
        "--output-count-mode",
        choices=["fixed", "gt_buffer", "tiered_gt_buffer", "sweep"],
        default="tiered_gt_buffer",
        help=(
            "How to choose final_output_count per example. "
            "fixed = same count for all rows; "
            "gt_buffer = len(ground_truth)+buffer; "
            "tiered_gt_buffer = smaller GT-sensitive buffer; "
            "sweep = run multiple fixed counts."
        ),
    )
    parser.add_argument(
        "--final-output-count",
        type=int,
        default=15,
        help="Used when --output-count-mode fixed. UI default is 15.",
    )
    parser.add_argument(
        "--gt-buffer",
        type=int,
        default=3,
        help="Added to ground-truth count when --output-count-mode gt_buffer.",
    )
    parser.add_argument(
        "--min-output-count",
        type=int,
        default=8,
        help="Minimum final_output_count for gt_buffer mode.",
    )
    parser.add_argument(
        "--max-output-count",
        type=int,
        default=25,
        help="Maximum final_output_count for any mode.",
    )
    parser.add_argument(
        "--sweep-counts",
        type=str,
        default="10,12,15,18,20",
        help="Comma-separated final_output_count values for sweep mode.",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-shuffle", action="store_true")
    parser.add_argument(
        "--include-source-ticket",
        action="store_true",
        help="Do not exclude the evaluated ticket from historical FAISS.",
    )
    parser.add_argument(
        "--allow-missing-ground-truth",
        action="store_true",
        help="Evaluate files even when no FAISS ground truth labels are found.",
    )
    parser.add_argument(
        "--ticket-ids",
        nargs="*",
        default=None,
        help="Optional explicit ticket IDs to evaluate. File stems must match these IDs.",
    )
    parser.add_argument(
        "--ticket-ids-file",
        default=None,
        help=(
            "Path to a text file with one ticket ID per line. Combined with --ticket-ids if both "
            "are provided. Lines starting with # and blank lines are ignored."
        ),
    )
    return parser.parse_args()


def load_ticket_ids_file(path: str | None) -> list[str]:
    if not path:
        return []
    file_path = Path(path)
    if not file_path.exists():
        raise SystemExit(f"--ticket-ids-file not found: {file_path}")
    ids: list[str] = []
    for raw_line in file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        ids.append(line)
    return ids


def apply_eval_llm_defaults() -> None:
    for key, value in EVAL_LLM_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def dynamic_output_count(gt_len: int, max_output_count: int = 25) -> int:
    gt_len = max(0, int(gt_len or 0))

    if gt_len <= 0:
        return min(max_output_count, 5)
    if gt_len <= 3:
        return min(max_output_count, gt_len + 1)
    if gt_len <= 6:
        return min(max_output_count, gt_len + 2)
    if gt_len <= 12:
        return min(max_output_count, gt_len + 3)
    return min(max_output_count, gt_len + 4)


def resolve_final_output_count(
    *,
    ground_truth: list[str],
    output_count_mode: str,
    final_output_count: int,
    gt_buffer: int,
    min_output_count: int,
    max_output_count: int,
) -> int:
    """Resolve the per-row final_output_count for non-sweep modes.

    fixed -> user-supplied count, clamped to [1, max_output_count].
    gt_buffer -> len(ground_truth) + gt_buffer, clamped to [min, max].
    tiered_gt_buffer -> GT-sensitive tiered count, clamped to max_output_count.
    """
    if output_count_mode == "fixed":
        return clamp_int(final_output_count, 1, max_output_count)
    if output_count_mode == "gt_buffer":
        return clamp_int(
            len(ground_truth or []) + gt_buffer,
            min_output_count,
            max_output_count,
        )
    if output_count_mode == "tiered_gt_buffer":
        return dynamic_output_count(len(ground_truth or []), max_output_count=max_output_count)
    raise ValueError(
        "resolve_final_output_count is not for sweep mode; iterate sweep_counts directly."
    )


def parse_sweep_counts(raw: str, max_output_count: int) -> list[int]:
    counts: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        counts.append(clamp_int(int(part), 1, max_output_count))

    seen: set[int] = set()
    out: list[int] = []
    for count in counts:
        if count in seen:
            continue
        seen.add(count)
        out.append(count)
    return out or [10, 12, 15, 18, 20]


def extract_debug_fields(payload: dict) -> dict:
    """Pull runtime/timing/prompt debug data from the pipeline payload, tolerating
    the slight differences between the streaming-route shape and the pipeline shape.
    """
    debug = payload.get("debug") or {}
    raw = payload.get("raw_response") or {}

    runtime_config = (
        payload.get("rag_runtime_config")
        or debug.get("rag_runtime_config")
        or {}
    )
    prompt_debug = debug.get("prompt_debug") or raw.get("prompt_debug") or {}
    timing_ms = debug.get("timing_ms") or raw.get("timing_ms") or {}
    candidate_window_counts = debug.get("candidate_window_counts") or {}

    return {
        "runtime_config": runtime_config if isinstance(runtime_config, dict) else {},
        "prompt_debug": prompt_debug if isinstance(prompt_debug, dict) else {},
        "timing_ms": timing_ms if isinstance(timing_ms, dict) else {},
        "candidate_window_counts": (
            candidate_window_counts if isinstance(candidate_window_counts, dict) else {}
        ),
    }


def count_selection_sources(selected_rows: Iterable[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in selected_rows or []:
        if not isinstance(row, dict):
            continue
        source = str(row.get("selection_source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return counts


def load_ground_truth(
    *,
    source: str,
    faiss_dir: Path,
    azure_index_name: str | None = None,
) -> dict[str, list[str]]:
    if str(source or "").strip().lower() == "azure":
        return load_ground_truth_from_azure(azure_index_name=azure_index_name)
    return load_ground_truth_from_faiss(faiss_dir)


def load_ground_truth_from_faiss(faiss_dir: Path) -> dict[str, list[str]]:
    docs_path = faiss_dir / "summary_docs.json"
    if not docs_path.exists():
        raise SystemExit(
            f"Ground truth file not found: {docs_path}. "
            "Build/copy the historical FAISS summary docs first."
        )

    with docs_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    docs = payload.get("summaries") if isinstance(payload, dict) else payload
    if not isinstance(docs, list):
        raise SystemExit(f"Unexpected summary docs shape in {docs_path}")

    out: dict[str, list[str]] = {}
    for row in docs:
        if not isinstance(row, dict):
            continue
        ticket_id = str(row.get("ticket_id") or row.get("key") or "").strip()
        if not ticket_id:
            continue
        labels = first_non_empty_list(
            row,
            "value_stream_names",
            "direct_vs_names",
            "value_stream_labels",
        )
        out[normalize_ticket_id(ticket_id)] = clean_name_list(labels)
    return out


def load_ground_truth_from_azure(azure_index_name: str | None = None) -> dict[str, list[str]]:
    from vs_app import settings as config
    from vs_app.ingestion.persistence.azure_historical_index import load_historical_summary_rows

    rows = load_historical_summary_rows(
        index_name=azure_index_name or config.HISTORICAL_AZURE_SEARCH_INDEX_NAME,
    )
    out: dict[str, list[str]] = {}
    for row in rows:
        ticket_id = str(row.get("ticket_id") or row.get("key") or "").strip()
        if not ticket_id:
            continue
        labels = first_non_empty_list(
            row,
            "value_stream_names",
            "direct_vs_names",
            "value_stream_labels",
        )
        out[normalize_ticket_id(ticket_id)] = clean_name_list(labels)
    return out


def discover_items(
    *,
    idea_cards_dir: Path,
    ground_truth_by_ticket: dict[str, list[str]],
    limit: int,
    seed: int,
    shuffle: bool,
    require_ground_truth: bool,
    ticket_ids: list[str] | None,
    min_ground_truth_streams: int = 2,
) -> list[EvaluationItem]:
    if not idea_cards_dir.exists():
        raise SystemExit(f"Idea-card folder not found: {idea_cards_dir}")

    explicit_ids = {normalize_ticket_id(value) for value in ticket_ids or []}
    candidates: list[EvaluationItem] = []

    for path in sorted(idea_cards_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        ticket_id = normalize_ticket_id(path.stem)
        if explicit_ids and ticket_id not in explicit_ids:
            continue
        ground_truth = ground_truth_by_ticket.get(ticket_id, [])
        if require_ground_truth and not ground_truth:
            continue
        if require_ground_truth and len(ground_truth) < max(1, min_ground_truth_streams):
            continue
        candidates.append(
            EvaluationItem(
                ticket_id=ticket_id,
                path=path,
                ground_truth=ground_truth,
            )
        )

    if shuffle:
        rng = random.Random(seed)
        rng.shuffle(candidates)

    return candidates[: max(0, limit)]


def run_batch(
    items: list[EvaluationItem],
    *,
    concurrency: int,
    fetch_count: int,
    historical_faiss_dir: str,
    historical_search_backend: str | None,
    historical_azure_index_name: str | None,
    exclude_source_ticket: bool,
    retries: int,
    retry_backoff_seconds: float,
    output_count_mode: str,
    final_output_count: int,
    gt_buffer: int,
    min_output_count: int,
    max_output_count: int,
    sweep_counts: list[int],
) -> list[TicketMetrics]:
    """Schedule eval jobs per mode and execute in parallel.

    fixed/gt_buffer/tiered_gt_buffer -> one job per item with the resolved count.
    sweep -> one job per (item, count) pair so each ticket runs once
                      per sweep value; output_count_mode is tagged
                      ``sweep_fixed_<count>`` so summary can group by count.
    """
    max_workers = max(1, concurrency)

    jobs: list[tuple[EvaluationItem, int, str, int]] = []
    if output_count_mode == "sweep":
        for item in items:
            for count in sweep_counts:
                jobs.append((item, count, f"sweep_fixed_{count}", 0))
    else:
        for item in items:
            count = (
                resolve_final_output_count(
                    ground_truth=item.ground_truth,
                    output_count_mode=output_count_mode,
                    final_output_count=final_output_count,
                    gt_buffer=gt_buffer,
                    min_output_count=min_output_count,
                    max_output_count=max_output_count,
                )
            )
            buffer_used = gt_buffer if output_count_mode == "gt_buffer" else 0
            jobs.append((item, count, output_count_mode, buffer_used))

    results: list[TicketMetrics] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_by_job = {
            executor.submit(
                evaluate_one_with_retries,
                item,
                fetch_count=fetch_count,
                historical_faiss_dir=historical_faiss_dir,
                historical_search_backend=historical_search_backend,
                historical_azure_index_name=historical_azure_index_name,
                exclude_source_ticket=exclude_source_ticket,
                retries=retries,
                retry_backoff_seconds=retry_backoff_seconds,
                final_output_count=count,
                output_count_mode=mode_tag,
                gt_buffer=buffer_used,
            ): (item, count, mode_tag, buffer_used)
            for (item, count, mode_tag, buffer_used) in jobs
        }

        completed = 0
        total = len(jobs)
        for future in as_completed(future_by_job):
            item, count, mode_tag, buffer_used = future_by_job[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                result = failed_result(
                    item,
                    exc,
                    output_count_mode=mode_tag,
                    final_output_count=count,
                    gt_buffer=buffer_used,
                )
            results.append(result)
            if result.status == "ok":
                print(
                    f"[{completed}/{total}] {result.ticket_id} n={result.final_output_count} "
                    f"{result.status} p={result.precision:.3f} r={result.recall:.3f} "
                    f"f1={result.f1:.3f} {result.elapsed_seconds:.1f}s"
                )
            else:
                print(f"[{completed}/{total}] {result.ticket_id} error {result.error}")

    return sorted(results, key=lambda row: (row.ticket_id, row.final_output_count))


def evaluate_one_with_retries(
    item: EvaluationItem,
    *,
    fetch_count: int,
    historical_faiss_dir: str,
    historical_search_backend: str | None,
    historical_azure_index_name: str | None,
    exclude_source_ticket: bool,
    retries: int,
    retry_backoff_seconds: float,
    final_output_count: int,
    output_count_mode: str = "fixed",
    gt_buffer: int = 0,
) -> TicketMetrics:
    attempts = max(1, int(retries) + 1)
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return evaluate_one(
                item,
                fetch_count=fetch_count,
                historical_faiss_dir=historical_faiss_dir,
                historical_search_backend=historical_search_backend,
                historical_azure_index_name=historical_azure_index_name,
                exclude_source_ticket=exclude_source_ticket,
                final_output_count=final_output_count,
                output_count_mode=output_count_mode,
                gt_buffer=gt_buffer,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not is_transient_gateway_error(exc):
                raise
            delay = max(0.0, float(retry_backoff_seconds)) * (2 ** (attempt - 1))
            print(
                f"[{item.ticket_id}] transient gateway error; retry "
                f"{attempt}/{attempts - 1} in {delay:.1f}s: {exc}"
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def evaluate_one(
    item: EvaluationItem,
    *,
    fetch_count: int,
    historical_faiss_dir: str,
    historical_search_backend: str | None,
    historical_azure_index_name: str | None,
    exclude_source_ticket: bool,
    final_output_count: int,
    output_count_mode: str = "fixed",
    gt_buffer: int = 0,
) -> TicketMetrics:
    from vs_app.integrations.files.idea_card_extractor import (
        build_foundational_metadata,
        extract_idea_card_text,
    )
    from vs_app.modules.rag.pipeline import select_value_streams

    start = time.perf_counter()
    text = extract_idea_card_text(input_path=item.path)
    foundational_metadata = build_foundational_metadata(text)
    exclude_ids = [item.ticket_id] if exclude_source_ticket else None

    payload = select_value_streams(
        text,
        fetch_count=fetch_count,
        final_output_count=final_output_count,
        historical_faiss_dir=historical_faiss_dir,
        historical_search_backend=historical_search_backend,
        historical_azure_index_name=historical_azure_index_name,
        exclude_ticket_ids=exclude_ids,
        foundational_value_streams_raw=foundational_metadata.get("foundational_value_streams_raw"),
        foundational_value_streams_canonical=foundational_metadata.get(
            "foundational_value_streams_canonical"
        ),
        foundational_value_stream_entity_ids=foundational_metadata.get(
            "foundational_value_stream_entity_ids"
        ),
        foundational_value_stream_matches=foundational_metadata.get(
            "foundational_value_stream_matches"
        ),
    )
    elapsed = time.perf_counter() - start

    selected_rows = payload.get("selected_value_streams", []) or []
    predicted = extract_names(selected_rows)
    metrics = compute_metrics(predicted, item.ground_truth)

    debug_fields = extract_debug_fields(payload)
    runtime_config = debug_fields["runtime_config"]
    prompt_debug = debug_fields["prompt_debug"]
    timing_ms = debug_fields["timing_ms"]
    window_counts = debug_fields["candidate_window_counts"]
    source_counts = count_selection_sources(selected_rows)
    foundational = foundational_metrics(payload)

    return TicketMetrics(
        ticket_id=item.ticket_id,
        file_name=item.path.name,
        status="ok",
        elapsed_seconds=round(elapsed, 3),
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1=metrics["f1"],
        true_positive_count=len(metrics["true_positives"]),
        false_positive_count=len(metrics["false_positives"]),
        false_negative_count=len(metrics["false_negatives"]),
        predicted_count=len(predicted),
        ground_truth_count=len(item.ground_truth),
        predicted_value_streams=predicted,
        ground_truth_value_streams=item.ground_truth,
        true_positives=metrics["true_positives"],
        false_positives=metrics["false_positives"],
        false_negatives=metrics["false_negatives"],
        selected_count=len(selected_rows),
        llm_candidate_count=len(payload.get("llm_candidates", []) or []),
        merged_candidate_count=len(payload.get("merged_candidate_value_streams", []) or []),
        historical_hit_count=len(payload.get("historical_ticket_hits", []) or []),
        excluded_ticket_ids=list(payload.get("historical_excluded_ticket_ids", []) or []),
        output_count_mode=output_count_mode,
        final_output_count=final_output_count,
        gt_buffer=gt_buffer,
        runtime_final_output_count=int(runtime_config.get("final_output_count") or 0),
        semantic_fetch_k=int(runtime_config.get("semantic_fetch_k") or 0),
        historical_ticket_fetch_k=int(runtime_config.get("historical_ticket_fetch_k") or 0),
        llm_candidate_window=int(runtime_config.get("llm_candidate_window") or 0),
        candidate_count=int(prompt_debug.get("candidate_count") or 0),
        prompt_chars=int(prompt_debug.get("prompt_chars") or 0),
        final_llm_ms=int(timing_ms.get("final_llm") or 0),
        total_ms=int(timing_ms.get("total") or 0),
        candidate_window_semantic_plus_historical=int(
            window_counts.get("semantic_plus_historical") or 0
        ),
        candidate_window_historical_only=int(window_counts.get("historical_only") or 0),
        candidate_window_semantic_only=int(window_counts.get("semantic_only") or 0),
        selection_source_llm_pick=int(source_counts.get("llm_pick") or 0),
        selection_source_safe_backfill=int(source_counts.get("safe_backfill") or 0),
        foundational_signals=list(payload.get("foundational_signals", []) or []),
        foundational_signal_source=str(payload.get("foundational_signal_source") or ""),
        foundational_signal_count=len(payload.get("foundational_signals", []) or []),
        foundational_candidate_count=int(foundational["foundational_candidate_count"]),
        selected_foundational_count=int(foundational["selected_foundational_count"]),
        missed_foundational_candidates=list(foundational["missed_foundational_candidates"]),
        foundational_recall=foundational["foundational_recall"],
    )


def is_transient_gateway_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "504",
            "gateway time-out",
            "gateway timeout",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "502",
            "503",
            "429",
            "500",
        )
    )


def failed_result(
    item: EvaluationItem,
    exc: BaseException,
    *,
    output_count_mode: str = "fixed",
    final_output_count: int = 0,
    gt_buffer: int = 0,
) -> TicketMetrics:
    return TicketMetrics(
        ticket_id=item.ticket_id,
        file_name=item.path.name,
        status="error",
        elapsed_seconds=0.0,
        precision=0.0,
        recall=0.0,
        f1=0.0,
        true_positive_count=0,
        false_positive_count=0,
        false_negative_count=len(item.ground_truth),
        predicted_count=0,
        ground_truth_count=len(item.ground_truth),
        predicted_value_streams=[],
        ground_truth_value_streams=item.ground_truth,
        true_positives=[],
        false_positives=[],
        false_negatives=item.ground_truth,
        selected_count=0,
        llm_candidate_count=0,
        merged_candidate_count=0,
        historical_hit_count=0,
        excluded_ticket_ids=[],
        output_count_mode=output_count_mode,
        final_output_count=final_output_count,
        gt_buffer=gt_buffer,
        error=f"{type(exc).__name__}: {exc}",
    )


def compute_metrics(predicted: Iterable[str], ground_truth: Iterable[str]) -> dict[str, Any]:
    predicted_by_key = {normalize_name(name): name for name in clean_name_list(predicted)}
    truth_by_key = {normalize_name(name): name for name in clean_name_list(ground_truth)}

    predicted_keys = set(predicted_by_key)
    truth_keys = set(truth_by_key)
    true_positive_keys = predicted_keys & truth_keys
    false_positive_keys = predicted_keys - truth_keys
    false_negative_keys = truth_keys - predicted_keys

    precision = safe_divide(len(true_positive_keys), len(predicted_keys))
    recall = safe_divide(len(true_positive_keys), len(truth_keys))
    f1 = safe_divide(2 * precision * recall, precision + recall)

    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "true_positives": sorted(truth_by_key[key] for key in true_positive_keys),
        "false_positives": sorted(predicted_by_key[key] for key in false_positive_keys),
        "false_negatives": sorted(truth_by_key[key] for key in false_negative_keys),
    }


def foundational_metrics(result: dict) -> dict:
    selected = result.get("selected_value_streams") or []
    merged = result.get("merged_candidate_value_streams") or []

    foundational_candidates = [
        row
        for row in merged
        if isinstance(row, dict) and row.get("foundational_signal")
    ]

    selected_keys = {
        normalize_name(row.get("entity_name") or "")
        for row in selected
        if isinstance(row, dict)
    }

    foundational_names = [
        row.get("entity_name")
        for row in foundational_candidates
        if row.get("entity_name")
    ]

    selected_foundational = [
        name
        for name in foundational_names
        if normalize_name(name) in selected_keys
    ]

    missed_foundational = [
        name
        for name in foundational_names
        if normalize_name(name) not in selected_keys
    ]

    denom = len(foundational_names)
    recall = len(selected_foundational) / denom if denom else None

    return {
        "foundational_candidate_count": denom,
        "selected_foundational_count": len(selected_foundational),
        "missed_foundational_candidates": missed_foundational,
        "foundational_recall": recall,
    }


def summarize(results: list[TicketMetrics]) -> dict[str, Any]:
    ok_rows = [row for row in results if row.status == "ok"]
    error_rows = [row for row in results if row.status != "ok"]

    tp = sum(row.true_positive_count for row in ok_rows)
    fp = sum(row.false_positive_count for row in ok_rows)
    fn = sum(row.false_negative_count for row in ok_rows)
    micro_precision = safe_divide(tp, tp + fp)
    micro_recall = safe_divide(tp, tp + fn)
    micro_f1 = safe_divide(2 * micro_precision * micro_recall, micro_precision + micro_recall)

    return {
        "total": len(results),
        "ok": len(ok_rows),
        "errors": len(error_rows),
        "precision": round(micro_precision, 4),
        "recall": round(micro_recall, 4),
        "f1": round(micro_f1, 4),
        "macro_precision": round(average(row.precision for row in ok_rows), 4),
        "macro_recall": round(average(row.recall for row in ok_rows), 4),
        "macro_f1": round(average(row.f1 for row in ok_rows), 4),
        "micro_precision": round(micro_precision, 4),
        "micro_recall": round(micro_recall, 4),
        "micro_f1": round(micro_f1, 4),
        "true_positive_count": tp,
        "false_positive_count": fp,
        "false_negative_count": fn,
        "avg_elapsed_seconds": round(average(row.elapsed_seconds for row in ok_rows), 3),
    }


def summarize_by_output_count(results: list[TicketMetrics]) -> dict[str, dict[str, Any]]:
    """Group sweep-mode results by final_output_count and run summarize() per group."""
    from collections import defaultdict

    groups: dict[int, list[TicketMetrics]] = defaultdict(list)
    for row in results:
        groups[int(row.final_output_count or 0)].append(row)

    return {
        f"final_output_count={count}": summarize(group_rows)
        for count, group_rows in sorted(groups.items())
    }


def extract_names(rows: Iterable[dict]) -> list[str]:
    return clean_name_list(str(row.get("entity_name") or "") for row in rows if isinstance(row, dict))


def first_non_empty_list(row: dict, *keys: str) -> list[Any]:
    for key in keys:
        value = row.get(key)
        if isinstance(value, list) and value:
            return value
    return []


def clean_name_list(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        clean = " ".join(str(value or "").strip().split())
        key = normalize_name(clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def normalize_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def normalize_ticket_id(value: str) -> str:
    return str(value or "").strip().upper()


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def average(values: Iterable[float]) -> float:
    nums = list(values)
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def serialize_result(row: TicketMetrics) -> dict[str, Any]:
    data = asdict(row)
    data["predicted_value_streams"] = list(row.predicted_value_streams)
    data["ground_truth_value_streams"] = list(row.ground_truth_value_streams)
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def write_jsonl(path: Path, results: list[TicketMetrics]) -> None:
    """One result per line — easier to grep/jq and stream-process than the bundled JSON."""
    with path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(serialize_result(row), ensure_ascii=False))
            fh.write("\n")


def write_csv(path: Path, results: list[TicketMetrics]) -> None:
    fieldnames = [
        "ticket_id",
        "file_name",
        "status",
        "elapsed_seconds",
        "output_count_mode",
        "final_output_count",
        "gt_buffer",
        "ground_truth_count",
        "selected_count",
        "predicted_count",
        "precision",
        "recall",
        "f1",
        "true_positive_count",
        "false_positive_count",
        "false_negative_count",
        "predicted_value_streams",
        "ground_truth_value_streams",
        "true_positives",
        "false_positives",
        "false_negatives",
        "llm_candidate_count",
        "merged_candidate_count",
        "historical_hit_count",
        "excluded_ticket_ids",
        "runtime_final_output_count",
        "semantic_fetch_k",
        "historical_ticket_fetch_k",
        "llm_candidate_window",
        "candidate_count",
        "prompt_chars",
        "final_llm_ms",
        "total_ms",
        "candidate_window_semantic_plus_historical",
        "candidate_window_historical_only",
        "candidate_window_semantic_only",
        "selection_source_llm_pick",
        "selection_source_safe_backfill",
        "foundational_signal_count",
        "foundational_signal_source",
        "foundational_candidate_count",
        "selected_foundational_count",
        "foundational_recall",
        "foundational_signals",
        "missed_foundational_candidates",
        "error",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            data = serialize_result(row)
            for key, value in list(data.items()):
                if isinstance(value, list):
                    data[key] = "; ".join(str(item) for item in value)
            writer.writerow({key: data.get(key, "") for key in fieldnames})


def print_summary(summary: dict[str, Any]) -> None:
    print("")
    print("Summary")
    print(f"  tickets:         {summary['ok']}/{summary['total']} ok ({summary['errors']} errors)")
    print(f"  precision:       {summary['precision']:.4f}")
    print(f"  recall:          {summary['recall']:.4f}")
    print(f"  f1:              {summary['f1']:.4f}")
    print(f"  avg seconds:     {summary['avg_elapsed_seconds']:.3f}")


if __name__ == "__main__":
    raise SystemExit(main())
