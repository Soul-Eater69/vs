"""Run batch evaluation for value-stream RAG over local idea-card files.

Example:
  py -3 scripts/evaluate_rag_batch.py --limit 100 --concurrency 4

The script expects local idea cards under ``idea_cards/`` by default and ground
truth labels in the historical FAISS ``summary_docs.json`` file. Historical RAG
hits default to the Azure historical summary index.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
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
    error: str = ""


def main() -> int:
    args = parse_args()

    idea_cards_dir = Path(args.idea_cards_dir)
    faiss_dir = Path(args.historical_faiss_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    ground_truth_by_ticket = load_ground_truth(faiss_dir)
    items = discover_items(
        idea_cards_dir=idea_cards_dir,
        ground_truth_by_ticket=ground_truth_by_ticket,
        limit=args.limit,
        seed=args.seed,
        shuffle=not args.no_shuffle,
        require_ground_truth=not args.allow_missing_ground_truth,
        ticket_ids=args.ticket_ids,
        min_ground_truth_streams=args.min_ground_truth_streams,
    )

    if not items:
        raise SystemExit("No idea cards with usable ground truth were found.")

    if len(items) < args.warn_below:
        print(
            f"Warning: only {len(items)} cards found; requested around {args.warn_below}-{args.limit}.",
            file=sys.stderr,
        )

    started = datetime.now(timezone.utc)
    print(
        f"Evaluating {len(items)} tickets with concurrency={args.concurrency}, "
        f"top_k={args.fetch_count}, exclude_source={not args.include_source_ticket}, "
        f"min_truth_streams={args.min_ground_truth_streams}, "
        f"historical_backend={args.historical_search_backend}"
    )

    results = run_batch(
        items,
        concurrency=args.concurrency,
        fetch_count=args.fetch_count,
        historical_faiss_dir=str(faiss_dir),
        historical_search_backend=args.historical_search_backend,
        historical_azure_index_name=args.historical_azure_index_name,
        exclude_source_ticket=not args.include_source_ticket,
    )

    summary = summarize(results)
    payload = {
        "created_at": started.isoformat(),
        "idea_cards_dir": str(idea_cards_dir),
        "historical_faiss_dir": str(faiss_dir),
        "historical_search_backend": args.historical_search_backend,
        "historical_azure_index_name": args.historical_azure_index_name,
        "limit": args.limit,
        "concurrency": args.concurrency,
        "fetch_count": args.fetch_count,
        "min_ground_truth_streams": args.min_ground_truth_streams,
        "exclude_source_ticket": not args.include_source_ticket,
        "summary": summary,
        "results": [serialize_result(row) for row in results],
    }

    json_path = output_dir / args.json_name
    csv_path = output_dir / args.csv_name
    write_json(json_path, payload)
    write_csv(csv_path, results)

    print_summary(summary)
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote CSV:  {csv_path}")
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
    parser.add_argument("--output-dir", default="output/rag_eval")
    parser.add_argument("--json-name", default="rag_batch_eval.json")
    parser.add_argument("--csv-name", default="rag_batch_eval.csv")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--warn-below",
        type=int,
        default=75,
        help="Print a warning when fewer than this many cards are available.",
    )
    parser.add_argument("--concurrency", type=int, default=4)
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
    return parser.parse_args()


def load_ground_truth(faiss_dir: Path) -> dict[str, list[str]]:
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
) -> list[TicketMetrics]:
    max_workers = max(1, concurrency)
    results: list[TicketMetrics] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_by_item = {
            executor.submit(
                evaluate_one,
                item,
                fetch_count=fetch_count,
                historical_faiss_dir=historical_faiss_dir,
                historical_search_backend=historical_search_backend,
                historical_azure_index_name=historical_azure_index_name,
                exclude_source_ticket=exclude_source_ticket,
            ): item
            for item in items
        }

        completed = 0
        total = len(items)
        for future in as_completed(future_by_item):
            item = future_by_item[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                result = failed_result(item, exc)
            results.append(result)
            if result.status == "ok":
                print(
                    f"[{completed}/{total}] {result.ticket_id} "
                    f"{result.status} p={result.precision:.3f} r={result.recall:.3f} "
                    f"f1={result.f1:.3f} {result.elapsed_seconds:.1f}s"
                )
            else:
                print(f"[{completed}/{total}] {result.ticket_id} error {result.error}")

    return sorted(results, key=lambda row: row.ticket_id)


def evaluate_one(
    item: EvaluationItem,
    *,
    fetch_count: int,
    historical_faiss_dir: str,
    historical_search_backend: str | None,
    historical_azure_index_name: str | None,
    exclude_source_ticket: bool,
) -> TicketMetrics:
    from vs_app.integrations.files.idea_card_extractor import extract_idea_card_text
    from vs_app.modules.rag.pipeline import select_value_streams

    start = time.perf_counter()
    text = extract_idea_card_text(input_path=item.path)
    exclude_ids = [item.ticket_id] if exclude_source_ticket else None

    payload = select_value_streams(
        text,
        fetch_count=fetch_count,
        historical_faiss_dir=historical_faiss_dir,
        historical_search_backend=historical_search_backend,
        historical_azure_index_name=historical_azure_index_name,
        exclude_ticket_ids=exclude_ids,
    )
    elapsed = time.perf_counter() - start

    predicted = extract_names(payload.get("selected_value_streams", []))
    metrics = compute_metrics(predicted, item.ground_truth)

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
        selected_count=len(payload.get("selected_value_streams", []) or []),
        llm_candidate_count=len(payload.get("llm_candidates", []) or []),
        merged_candidate_count=len(payload.get("merged_candidate_value_streams", []) or []),
        historical_hit_count=len(payload.get("historical_ticket_hits", []) or []),
        excluded_ticket_ids=list(payload.get("historical_excluded_ticket_ids", []) or []),
    )


def failed_result(item: EvaluationItem, exc: BaseException) -> TicketMetrics:
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


def write_csv(path: Path, results: list[TicketMetrics]) -> None:
    fieldnames = [
        "ticket_id",
        "file_name",
        "status",
        "elapsed_seconds",
        "precision",
        "recall",
        "f1",
        "true_positive_count",
        "false_positive_count",
        "false_negative_count",
        "predicted_count",
        "ground_truth_count",
        "predicted_value_streams",
        "ground_truth_value_streams",
        "true_positives",
        "false_positives",
        "false_negatives",
        "selected_count",
        "llm_candidate_count",
        "merged_candidate_count",
        "historical_hit_count",
        "excluded_ticket_ids",
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
