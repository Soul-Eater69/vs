"""Build a deterministic false-negative review for stage prediction evaluation."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
from math import ceil
import os
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.modules.stages.stage_catalog import get_value_stream_catalog_entry, load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import normalize_ticket_key


try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - exercised only when rapidfuzz is unavailable
    fuzz = None


DEFAULT_EVAL_CSV = Path("output/stage_prediction_eval/stage_prediction_eval.csv")
DEFAULT_PREDICTIONS_JSON = Path("output/stage_prediction_eval/stage_predictions.json")
DEFAULT_DATASET_JSON = Path("output/stage_prediction_eval/stage_prediction_dataset_gt_patched.json")
DEFAULT_STAGE_CATALOG = Path("data/value_stream_stage_map.json")
DEFAULT_OUTPUT_DIR = Path("output/stage_prediction_eval")

REVIEW_CSV = "false_negative_review.csv"
REVIEW_MD = "false_negative_review.md"
SUMMARY_JSON = "false_negative_summary.json"

REASONS = [
    "EXPLICIT_EVIDENCE_MISSED",
    "IMPLIED_EVIDENCE_MISSED",
    "NEAR_MISS",
    "NOT_IN_CONTEXT",
    "CATALOG_AMBIGUITY",
    "GT_TOO_BROAD",
]

CSV_COLUMNS = [
    "ticket_id",
    "value_stream_name",
    "missed_gt_stage",
    "predicted_stages",
    "false_positive_stages",
    "gt_stages",
    "raw_context_has_exact_stage_name",
    "raw_context_has_stage_words",
    "generated_summary_has_exact_stage_name",
    "generated_summary_has_stage_words",
    "predicted_near_miss_stage",
    "near_miss_score",
    "likely_reason",
    "context_excerpt",
    "generated_summary_excerpt",
    "stage_description",
    "stage_keywords",
    "prediction_reasons",
]

STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "initiate",
    "into",
    "manage",
    "of",
    "or",
    "perform",
    "process",
    "the",
    "to",
    "with",
}

BROAD_STAGE_NAMES = {
    "determine product concept",
    "product and benefit setup",
    "evaluate market conditions",
    "initiate request inquiry",
    "configure products and validate request",
}


def main() -> int:
    config = load_runtime_config()
    eval_rows = load_eval_csv(Path(config["eval_csv"]))
    predictions_payload = load_json(Path(config["predictions_json"]))
    dataset_payload = load_json(Path(config["dataset_json"]))
    stage_catalog = load_stage_catalog(path=config["stage_catalog"], source="json")

    review_rows = build_false_negative_rows(
        eval_rows=eval_rows,
        predictions_payload=predictions_payload,
        dataset_payload=dataset_payload,
        stage_catalog=stage_catalog,
    )
    output_dir = Path(config["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = build_summary(
        review_rows=review_rows,
        eval_rows=eval_rows,
        config=config,
        output_dir=output_dir,
    )
    write_review_csv(output_dir / REVIEW_CSV, review_rows)
    write_markdown_summary(output_dir / REVIEW_MD, summary, review_rows)
    write_json(output_dir / SUMMARY_JSON, summary)

    print("Stage false-negative analysis complete", flush=True)
    print(f"FN rows: {len(review_rows)}", flush=True)
    print(f"Output directory: {output_dir}", flush=True)
    return 0


def load_runtime_config() -> dict[str, Any]:
    return {
        "eval_csv": Path(os.getenv("STAGE_FN_EVAL_CSV", str(DEFAULT_EVAL_CSV))),
        "predictions_json": Path(
            os.getenv("STAGE_FN_PREDICTIONS_JSON", str(DEFAULT_PREDICTIONS_JSON))
        ),
        "dataset_json": Path(os.getenv("STAGE_FN_DATASET_JSON", str(DEFAULT_DATASET_JSON))),
        "stage_catalog": Path(os.getenv("STAGE_FN_STAGE_CATALOG", str(DEFAULT_STAGE_CATALOG))),
        "output_dir": Path(os.getenv("STAGE_FN_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
    }


def build_false_negative_rows(
    *,
    eval_rows: list[dict[str, str]],
    predictions_payload: dict[str, Any],
    dataset_payload: dict[str, Any],
    stage_catalog: dict[str, Any],
) -> list[dict[str, Any]]:
    prediction_reason_lookup = build_prediction_reason_lookup(predictions_payload)
    review_rows: list[dict[str, Any]] = []

    for eval_row in eval_rows:
        ticket_id = normalize_ticket_key(eval_row.get("idmt_key"))
        value_stream_name = clean_text(eval_row.get("value_stream_name"))
        gt_stages = parse_stage_list(eval_row.get("gt_stages"))
        predicted_stages = parse_stage_list(eval_row.get("predicted_stages"))
        false_positive_stages = parse_stage_list(eval_row.get("fp"))
        missed_stages = parse_stage_list(eval_row.get("fn"))
        if not ticket_id or not value_stream_name or not missed_stages:
            continue

        idea_card = idea_card_for_ticket(dataset_payload, ticket_id)
        raw_context = original_context_text(idea_card)
        generated_summary = clean_text(idea_card.get("generated_summary"))
        prediction_reasons = prediction_reason_lookup.get((ticket_id, value_stream_name), "")
        if not prediction_reasons:
            prediction_reasons = clean_text(eval_row.get("prediction_reasons"))

        for missed_stage in missed_stages:
            review_rows.append(
                build_false_negative_row(
                    ticket_id=ticket_id,
                    value_stream_name=value_stream_name,
                    missed_stage=missed_stage,
                    gt_stages=gt_stages,
                    predicted_stages=predicted_stages,
                    false_positive_stages=false_positive_stages,
                    raw_context=raw_context,
                    generated_summary=generated_summary,
                    prediction_reasons=prediction_reasons,
                    stage_catalog=stage_catalog,
                )
            )

    return review_rows


def build_false_negative_row(
    *,
    ticket_id: str,
    value_stream_name: str,
    missed_stage: str,
    gt_stages: list[str],
    predicted_stages: list[str],
    false_positive_stages: list[str],
    raw_context: str,
    generated_summary: str,
    prediction_reasons: str,
    stage_catalog: dict[str, Any],
) -> dict[str, Any]:
    missed_stage = clean_text(missed_stage)
    stage_meta = stage_metadata(stage_catalog, value_stream_name, missed_stage)
    stage_tokens = important_stage_tokens(missed_stage)
    stage_keywords = important_stage_tokens(
        " ".join([missed_stage, stage_meta.get("metadata_text", "")])
    )
    raw_norm = normalize_text(raw_context)
    summary_norm = normalize_text(generated_summary)
    stage_norm = normalize_text(missed_stage)

    raw_exact = bool(stage_norm and stage_norm in raw_norm)
    summary_exact = bool(stage_norm and stage_norm in summary_norm)
    raw_stage_words = stage_word_match(stage_tokens, raw_norm)
    summary_stage_words = stage_word_match(stage_tokens, summary_norm)
    context_excerpt = best_context_excerpt(raw_context, stage_keywords or stage_tokens)
    summary_excerpt = best_context_excerpt(generated_summary, stage_keywords or stage_tokens)
    context_score = fuzzy_score(missed_stage, context_excerpt)
    near_miss_stage, near_miss_score = best_near_miss(missed_stage, predicted_stages)
    catalog_ambiguity = has_catalog_ambiguity(
        stage_catalog=stage_catalog,
        value_stream_name=value_stream_name,
        missed_stage=missed_stage,
        predicted_stages=predicted_stages,
    )
    matched_token_count = count_stage_token_matches(stage_tokens, raw_norm)
    likely_reason = classify_false_negative(
        raw_exact=raw_exact,
        context_score=context_score,
        raw_stage_words=raw_stage_words,
        near_miss_score=near_miss_score,
        catalog_ambiguity=catalog_ambiguity,
        matched_token_count=matched_token_count,
        missed_stage=missed_stage,
        raw_context=raw_context,
    )

    return {
        "ticket_id": ticket_id,
        "value_stream_name": value_stream_name,
        "missed_gt_stage": missed_stage,
        "predicted_stages": join_stage_list(predicted_stages),
        "false_positive_stages": join_stage_list(false_positive_stages),
        "gt_stages": join_stage_list(gt_stages),
        "raw_context_has_exact_stage_name": raw_exact,
        "raw_context_has_stage_words": raw_stage_words,
        "generated_summary_has_exact_stage_name": summary_exact,
        "generated_summary_has_stage_words": summary_stage_words,
        "predicted_near_miss_stage": near_miss_stage,
        "near_miss_score": near_miss_score,
        "likely_reason": likely_reason,
        "context_excerpt": context_excerpt,
        "generated_summary_excerpt": summary_excerpt,
        "stage_description": stage_meta.get("stage_description", ""),
        "stage_keywords": " | ".join(stage_keywords),
        "prediction_reasons": prediction_reasons,
    }


def classify_false_negative(
    *,
    raw_exact: bool,
    context_score: float,
    raw_stage_words: bool,
    near_miss_score: float,
    catalog_ambiguity: bool,
    matched_token_count: int,
    missed_stage: str,
    raw_context: str,
) -> str:
    if raw_exact or context_score >= 90:
        return "EXPLICIT_EVIDENCE_MISSED"
    if near_miss_score >= 75:
        return "NEAR_MISS"
    if raw_stage_words:
        return "IMPLIED_EVIDENCE_MISSED"
    if catalog_ambiguity:
        return "CATALOG_AMBIGUITY"
    if raw_context and (
        matched_token_count > 0
        or normalize_text(missed_stage) in BROAD_STAGE_NAMES
        or len(important_stage_tokens(missed_stage)) <= 2
    ):
        return "GT_TOO_BROAD"
    return "NOT_IN_CONTEXT"


def has_catalog_ambiguity(
    *,
    stage_catalog: dict[str, Any],
    value_stream_name: str,
    missed_stage: str,
    predicted_stages: list[str],
) -> bool:
    if not predicted_stages:
        return False
    missed_meta = stage_metadata(stage_catalog, value_stream_name, missed_stage)
    missed_text = " ".join([missed_stage, missed_meta.get("metadata_text", "")])
    missed_tokens = set(important_stage_tokens(missed_text))
    for predicted_stage in predicted_stages:
        predicted_meta = stage_metadata(stage_catalog, value_stream_name, predicted_stage)
        predicted_text = " ".join([predicted_stage, predicted_meta.get("metadata_text", "")])
        score = fuzzy_score(missed_text, predicted_text)
        predicted_tokens = set(important_stage_tokens(predicted_text))
        overlap = safe_div(len(missed_tokens & predicted_tokens), len(missed_tokens | predicted_tokens))
        if score >= 75 or overlap >= 0.35:
            return True
    return False


def build_prediction_reason_lookup(predictions_payload: dict[str, Any]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for ticket in predictions_payload.get("tickets") or []:
        if not isinstance(ticket, dict):
            continue
        ticket_id = normalize_ticket_key(ticket.get("ticket_id"))
        if not ticket_id:
            continue
        for row in ticket.get("value_stream_predictions") or []:
            if not isinstance(row, dict):
                continue
            value_stream_name = clean_text(row.get("value_stream_name"))
            if not value_stream_name:
                continue
            reason_rows: list[str] = []
            for stage in row.get("selected_stages") or []:
                if not isinstance(stage, dict):
                    continue
                stage_name = clean_text(stage.get("canonical_stage") or stage.get("stage_name"))
                reason = clean_text(stage.get("reason"))
                evidence = clean_text(stage.get("evidence_summary"))
                detail = " / ".join(part for part in [reason, evidence] if part)
                if stage_name and detail:
                    reason_rows.append(f"{stage_name}: {detail}")
                elif detail:
                    reason_rows.append(detail)
            out[(ticket_id, value_stream_name)] = " | ".join(reason_rows)
    return out


def stage_metadata(
    catalog: dict[str, Any],
    value_stream_name: str,
    stage_name: str,
) -> dict[str, str]:
    entry = get_value_stream_catalog_entry(value_stream_name, catalog) or {}
    target = normalize_text(stage_name)
    for stage in entry.get("stages") or []:
        if not isinstance(stage, dict):
            if normalize_text(stage) == target:
                return {"stage_description": "", "metadata_text": ""}
            continue
        candidate_name = clean_text(
            stage.get("name")
            or stage.get("stage_name")
            or stage.get("value_stream_stage_name")
            or stage.get("display_name")
            or stage.get("stage_display_name")
        )
        if normalize_text(candidate_name) != target:
            continue
        description_parts = [
            clean_text(stage.get("description") or stage.get("stage_description")),
            clean_text(stage.get("entrance_criteria") or stage.get("stage_entrance_criteria")),
            clean_text(stage.get("exit_criteria") or stage.get("stage_exit_criteria")),
            clean_text(stage.get("value_items") or stage.get("stage_value_items")),
            clean_text(stage.get("stakeholders") or stage.get("stage_stakeholders")),
        ]
        stage_description = " ".join(part for part in description_parts if part)
        return {
            "stage_description": stage_description,
            "metadata_text": stage_description,
        }
    return {"stage_description": "", "metadata_text": ""}


def idea_card_for_ticket(dataset_payload: dict[str, Any], ticket_id: str) -> dict[str, Any]:
    tickets = dataset_payload.get("tickets") or {}
    direct = tickets.get(ticket_id)
    if isinstance(direct, dict):
        return direct.get("idea_card") or {}
    for key, row in tickets.items():
        if normalize_ticket_key(key) == ticket_id and isinstance(row, dict):
            return row.get("idea_card") or {}
    return {}


def original_context_text(idea_card: dict[str, Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for key in ("summary", "description", "idea_card_text", "attachment_text", "extracted_text"):
        text = clean_text(idea_card.get(key))
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n\n".join(parts)


def parse_stage_list(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def join_stage_list(values: list[str]) -> str:
    return " | ".join(clean_text(value) for value in values if clean_text(value))


def normalize_text(text: Any) -> str:
    value = str(text or "").lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def important_stage_tokens(stage_name: str) -> list[str]:
    tokens = [
        token
        for token in normalize_text(stage_name).split()
        if len(token) > 1 and token not in STOPWORDS
    ]
    if tokens:
        return dedupe(tokens)
    return dedupe([token for token in normalize_text(stage_name).split() if token])


def stage_word_match(stage_tokens: list[str], normalized_context: str) -> bool:
    if not stage_tokens or not normalized_context:
        return False
    matched = count_stage_token_matches(stage_tokens, normalized_context)
    return matched >= max(1, ceil(len(stage_tokens) * 0.5))


def count_stage_token_matches(stage_tokens: list[str], normalized_context: str) -> int:
    if not normalized_context:
        return 0
    context_tokens = set(normalized_context.split())
    return sum(1 for token in stage_tokens if token in context_tokens)


def best_context_excerpt(text: str, keywords: list[str], window: int = 300) -> str:
    source = clean_text(text)
    if not source:
        return ""
    if not keywords:
        return source[:window]

    lower_source = source.lower()
    lower_keywords = [keyword.lower() for keyword in keywords if keyword]
    candidate_positions: list[int] = []
    for keyword in lower_keywords:
        start = 0
        while True:
            found = lower_source.find(keyword, start)
            if found < 0:
                break
            candidate_positions.append(found)
            start = found + max(1, len(keyword))

    if not candidate_positions:
        return source[:window]

    best_start = 0
    best_score = -1
    for position in candidate_positions:
        start = max(0, position - window // 2)
        end = min(len(source), start + window)
        segment = lower_source[start:end]
        score = sum(segment.count(keyword) for keyword in lower_keywords)
        if score > best_score:
            best_score = score
            best_start = start

    excerpt = source[best_start : min(len(source), best_start + window)]
    if best_start > 0:
        excerpt = "..." + excerpt
    if best_start + window < len(source):
        excerpt += "..."
    return excerpt


def best_near_miss(missed_stage: str, predicted_stages: list[str]) -> tuple[str, float]:
    best_stage = ""
    best_score = 0.0
    for predicted_stage in predicted_stages:
        score = fuzzy_score(missed_stage, predicted_stage)
        if score > best_score:
            best_stage = predicted_stage
            best_score = score
    return best_stage, round(best_score, 3)


def fuzzy_score(left: str, right: str) -> float:
    left_text = clean_text(left)
    right_text = clean_text(right)
    if not left_text or not right_text:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_set_ratio(left_text, right_text))
    return SequenceMatcher(None, normalize_text(left_text), normalize_text(right_text)).ratio() * 100


def safe_div(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def build_summary(
    *,
    review_rows: list[dict[str, Any]],
    eval_rows: list[dict[str, str]],
    config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    reason_counts = Counter(row["likely_reason"] for row in review_rows)
    missed_stage_counts = Counter(row["missed_gt_stage"] for row in review_rows)
    value_stream_counts = Counter(row["value_stream_name"] for row in review_rows)
    fp_counts = false_positive_counts(eval_rows)
    near_miss_counts = Counter(
        (row["missed_gt_stage"], row["predicted_near_miss_stage"])
        for row in review_rows
        if row.get("predicted_near_miss_stage") and float(row.get("near_miss_score") or 0.0) >= 75
    )

    return {
        "source": "stage_false_negative_analysis",
        "generated_at": utc_now(),
        "inputs": {
            "eval_csv": str(config["eval_csv"]),
            "predictions_json": str(config["predictions_json"]),
            "dataset_json": str(config["dataset_json"]),
            "stage_catalog": str(config["stage_catalog"]),
        },
        "outputs": {
            "false_negative_review_csv": str(output_dir / REVIEW_CSV),
            "false_negative_review_md": str(output_dir / REVIEW_MD),
            "false_negative_summary_json": str(output_dir / SUMMARY_JSON),
        },
        "total_fn_instances": len(review_rows),
        "reason_counts": dict(sorted(reason_counts.items())),
        "top_missed_gt_stages": counter_rows(missed_stage_counts),
        "top_fn_value_streams": counter_rows(value_stream_counts),
        "top_near_misses": [
            {"missed_gt_stage": gt, "predicted_stage": pred, "count": count}
            for (gt, pred), count in near_miss_counts.most_common(20)
        ],
        "false_positive_summary": counter_rows(fp_counts),
    }


def false_positive_counts(eval_rows: list[dict[str, str]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in eval_rows:
        counter.update(parse_stage_list(row.get("fp")))
    return counter


def counter_rows(counter: Counter[str], limit: int = 20) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})


def write_markdown_summary(
    path: Path,
    summary: dict[str, Any],
    review_rows: list[dict[str, Any]],
) -> None:
    reason_counts = Counter(row["likely_reason"] for row in review_rows)
    missed_stage_counts = Counter(row["missed_gt_stage"] for row in review_rows)
    value_stream_counts = Counter(row["value_stream_name"] for row in review_rows)
    near_miss_counts = Counter(
        (row["missed_gt_stage"], row["predicted_near_miss_stage"])
        for row in review_rows
        if row.get("predicted_near_miss_stage") and float(row.get("near_miss_score") or 0.0) >= 75
    )
    fp_counts = Counter(
        {
            clean_text(row.get("name")): int(row.get("count") or 0)
            for row in summary.get("false_positive_summary") or []
            if clean_text(row.get("name"))
        }
    )

    lines = [
        "# False Negative Review Summary",
        "",
        "## Overall Counts",
        f"- Total FN instances: {summary.get('total_fn_instances', 0)}",
        f"- Reason counts: {len(reason_counts)}",
        f"- Top missed GT stages: {len(missed_stage_counts)}",
        f"- Top FN Value Streams: {len(value_stream_counts)}",
        "",
        "## Reason Counts",
        markdown_counter_table("Reason", reason_counts),
        "",
        "## Top Missed GT Stages",
        markdown_counter_table("Stage", missed_stage_counts),
        "",
        "## Top Value Streams By FN Count",
        markdown_counter_table("Value Stream", value_stream_counts),
        "",
        "## Top Near Misses",
        markdown_near_miss_table(near_miss_counts),
        "",
        "## False Positive Summary",
        markdown_counter_table("Predicted Stage", fp_counts),
        "",
        "## Sample Rows By Reason",
    ]

    rows_by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in review_rows:
        rows_by_reason[row["likely_reason"]].append(row)

    for reason in REASONS:
        lines.extend(["", f"### {reason}"])
        samples = rows_by_reason.get(reason) or []
        if not samples:
            lines.append("_No rows._")
            continue
        for row in samples[:5]:
            excerpt = clean_text(row.get("context_excerpt"))
            lines.append(
                "- "
                f"{row.get('ticket_id')} | {row.get('value_stream_name')} | "
                f"missed={row.get('missed_gt_stage')} | "
                f"near_miss={row.get('predicted_near_miss_stage')} "
                f"({row.get('near_miss_score')}) | "
                f"excerpt={excerpt[:220]}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def markdown_counter_table(label: str, counter: Counter[str], limit: int = 20) -> str:
    lines = [f"| {label} | Count |", "|---|---:|"]
    for name, count in counter.most_common(limit):
        lines.append(f"| {escape_md(name)} | {count} |")
    if len(lines) == 2:
        lines.append("| _None_ | 0 |")
    return "\n".join(lines)


def markdown_near_miss_table(counter: Counter[tuple[str, str]], limit: int = 20) -> str:
    lines = ["| GT Stage | Predicted Stage | Count |", "|---|---|---:|"]
    for (gt_stage, predicted_stage), count in counter.most_common(limit):
        lines.append(f"| {escape_md(gt_stage)} | {escape_md(predicted_stage)} | {count} |")
    if len(lines) == 2:
        lines.append("| _None_ | _None_ | 0 |")
    return "\n".join(lines)


def escape_md(value: Any) -> str:
    return clean_text(value).replace("|", "\\|")


def load_eval_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"Stage eval CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_json(path: Path) -> Any:
    if not path.exists():
        raise SystemExit(f"Input JSON not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
