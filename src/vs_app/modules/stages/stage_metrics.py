from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def compute_stage_metrics(gt_stages: list[str], predicted_stages: list[str]) -> dict[str, Any]:
    gt = set(_clean_list(gt_stages))
    predicted = set(_clean_list(predicted_stages))
    correct = sorted(gt & predicted)
    false_positives = sorted(predicted - gt)
    false_negatives = sorted(gt - predicted)
    precision = _safe_div(len(correct), len(predicted))
    recall = _safe_div(len(correct), len(gt))

    return {
        "gt_stages": sorted(gt),
        "predicted_stages": sorted(predicted),
        "correct_stages": correct,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def evaluate_stage_predictions(
    rows: list[dict[str, Any]],
    *,
    unresolved_gt_stage_count: int = 0,
    duplicate_raw_mentions_collapsed_count: int = 0,
) -> dict[str, Any]:
    evaluated_rows: list[dict[str, Any]] = []
    total_tp = total_fp = total_fn = 0

    for row in rows:
        metrics = compute_stage_metrics(
            list(row.get("gt_stages") or []),
            list(row.get("predicted_stages") or []),
        )
        merged = {**row, **metrics}
        evaluated_rows.append(merged)
        total_tp += len(metrics["correct_stages"])
        total_fp += len(metrics["false_positives"])
        total_fn += len(metrics["false_negatives"])

    micro_precision = _safe_div(total_tp, total_tp + total_fp)
    micro_recall = _safe_div(total_tp, total_tp + total_fn)
    macro_precision = _avg(row["precision"] for row in evaluated_rows)
    macro_recall = _avg(row["recall"] for row in evaluated_rows)
    macro_f1 = _avg(row["f1"] for row in evaluated_rows)

    missed = Counter()
    extra = Counter()
    misses_by_ticket: dict[str, int] = defaultdict(int)
    for row in evaluated_rows:
        missed.update(row["false_negatives"])
        extra.update(row["false_positives"])
        misses_by_ticket[str(row.get("ticket_id") or "")] += len(row["false_negatives"])

    return {
        "summary": {
            "row_count": len(evaluated_rows),
            "micro_precision": micro_precision,
            "micro_recall": micro_recall,
            "micro_f1": _f1(micro_precision, micro_recall),
            "macro_precision": macro_precision,
            "macro_recall": macro_recall,
            "macro_f1": macro_f1,
            "true_positive_count": total_tp,
            "false_positive_count": total_fp,
            "false_negative_count": total_fn,
            "unresolved_gt_stage_count": unresolved_gt_stage_count,
            "duplicate_raw_mentions_collapsed_count": duplicate_raw_mentions_collapsed_count,
            "top_missed_stages": _counter_rows(missed),
            "top_extra_predicted_stages": _counter_rows(extra),
            "worst_tickets_by_misses": [
                {"ticket_id": ticket_id, "miss_count": count}
                for ticket_id, count in sorted(
                    misses_by_ticket.items(),
                    key=lambda item: (-item[1], item[0]),
                )
                if count
            ][:20],
        },
        "rows": evaluated_rows,
    }


def write_stage_eval_outputs(result: dict[str, Any], output_dir: str | Path) -> None:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = list(result.get("rows") or [])

    (out_dir / "stage_eval.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with (out_dir / "stage_eval.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    fieldnames = [
        "ticket_id",
        "value_stream_name",
        "gt_stages",
        "predicted_stages",
        "correct_stages",
        "false_positives",
        "false_negatives",
        "precision",
        "recall",
        "f1",
    ]
    with (out_dir / "stage_eval.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: json.dumps(row.get(field), ensure_ascii=False)
                    if isinstance(row.get(field), list)
                    else row.get(field)
                    for field in fieldnames
                }
            )


def unresolved_gt_stage_count(ground_truth: dict[str, Any]) -> int:
    total = 0
    for ticket in (ground_truth.get("tickets") or {}).values():
        for theme in ticket.get("linked_themes") or []:
            total += len(theme.get("unresolved_stage_mentions") or [])
    return total


def duplicate_raw_mentions_collapsed_count(ground_truth: dict[str, Any]) -> int:
    total = 0
    for ticket in (ground_truth.get("tickets") or {}).values():
        for theme in ticket.get("linked_themes") or []:
            for stage in theme.get("verified_stages") or []:
                total += max(0, len(stage.get("raw_mentions") or []) - 1)
    return total


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _counter_rows(counter: Counter) -> list[dict[str, Any]]:
    return [
        {"stage": stage, "count": count}
        for stage, count in counter.most_common(20)
    ]


def _safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0 if numerator == 0 else 0.0
    return round(numerator / denominator, 6)


def _f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def _avg(values: Any) -> float:
    vals = list(values)
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), 6)


__all__ = [
    "compute_stage_metrics",
    "duplicate_raw_mentions_collapsed_count",
    "evaluate_stage_predictions",
    "unresolved_gt_stage_count",
    "write_stage_eval_outputs",
]
