from pathlib import Path
import shutil
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import evaluate_rag_batch as eval_script
from evaluate_rag_batch import (
    apply_eval_llm_defaults,
    build_miss_bucket_summary,
    build_missed_ground_truth_debug,
    classify_ground_truth_miss,
    compute_metrics,
    discover_items,
    EvaluationItem,
    dynamic_output_count,
    is_transient_gateway_error,
    load_ground_truth_from_azure,
    main,
    resolve_final_output_count,
    summarize,
    TicketMetrics,
)


def _ticket_metrics(
    ticket_id: str = "IDMT-1",
    *,
    missed_ground_truth_debug: list[dict] | None = None,
) -> TicketMetrics:
    return TicketMetrics(
        ticket_id=ticket_id,
        file_name=f"{ticket_id}.txt",
        status="ok",
        elapsed_seconds=1.0,
        vs_seconds=1.0,
        stage_seconds=0.0,
        total_seconds=1.0,
        precision=0.0,
        recall=0.0,
        f1=0.0,
        true_positive_count=0,
        false_positive_count=0,
        false_negative_count=len(missed_ground_truth_debug or []),
        predicted_count=0,
        ground_truth_count=len(missed_ground_truth_debug or []),
        predicted_value_streams=[],
        ground_truth_value_streams=[],
        true_positives=[],
        false_positives=[],
        false_negatives=[],
        selected_count=0,
        llm_candidate_count=0,
        merged_candidate_count=0,
        historical_hit_count=0,
        excluded_ticket_ids=[],
        missed_ground_truth_debug=missed_ground_truth_debug or [],
    )


def test_compute_metrics_reports_ticket_level_precision_recall() -> None:
    metrics = compute_metrics(
        ["Establish Product Offering", "Issue Payment", "Issue Payment"],
        ["Establish Product Offering", "Manage Member Care"],
    )

    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 0.5
    assert metrics["true_positives"] == ["Establish Product Offering"]
    assert metrics["false_positives"] == ["Issue Payment"]
    assert metrics["false_negatives"] == ["Manage Member Care"]


def test_tiered_output_count_uses_small_gt_sensitive_buffer() -> None:
    assert dynamic_output_count(2) == 3
    assert dynamic_output_count(3) == 4
    assert dynamic_output_count(5) == 7
    assert dynamic_output_count(8) == 11
    assert dynamic_output_count(12) == 15
    assert dynamic_output_count(16) == 20

    assert resolve_final_output_count(
        ground_truth=["A", "B"],
        output_count_mode="tiered_gt_buffer",
        final_output_count=15,
        gt_buffer=3,
        min_output_count=8,
        max_output_count=25,
    ) == 3


def test_discover_items_samples_matching_idea_cards_with_ground_truth() -> None:
    tmp_dir = Path("pytest-cache-files-rag-eval-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        (tmp_dir / "IDMT-1.txt").write_text("one", encoding="utf-8")
        (tmp_dir / "IDMT-2.md").write_text("two", encoding="utf-8")
        (tmp_dir / "IDMT-3.png").write_text("ignored", encoding="utf-8")

        items = discover_items(
            idea_cards_dir=tmp_dir,
            ground_truth_by_ticket={
                "IDMT-1": ["A"],
                "IDMT-2": ["B"],
                "IDMT-3": ["C"],
            },
            limit=1,
            seed=7,
            shuffle=False,
            require_ground_truth=True,
            ticket_ids=None,
            min_ground_truth_streams=1,
        )

        assert [item.ticket_id for item in items] == ["IDMT-1"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_discover_items_skips_single_label_tickets_by_default() -> None:
    tmp_dir = Path("pytest-cache-files-rag-eval-min-truth-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        (tmp_dir / "IDMT-1.txt").write_text("one", encoding="utf-8")
        (tmp_dir / "IDMT-2.txt").write_text("two", encoding="utf-8")

        items = discover_items(
            idea_cards_dir=tmp_dir,
            ground_truth_by_ticket={
                "IDMT-1": ["A"],
                "IDMT-2": ["A", "B"],
            },
            limit=10,
            seed=7,
            shuffle=False,
            require_ground_truth=True,
            ticket_ids=None,
        )

        assert [item.ticket_id for item in items] == ["IDMT-2"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_summarize_reports_macro_and_micro_scores() -> None:
    from evaluate_rag_batch import TicketMetrics

    rows = [
        TicketMetrics(
            ticket_id="IDMT-1",
            file_name="IDMT-1.txt",
            status="ok",
            elapsed_seconds=1.0,
            vs_seconds=1.0,
            stage_seconds=0.0,
            total_seconds=1.0,
            precision=1.0,
            recall=0.5,
            f1=0.6667,
            true_positive_count=1,
            false_positive_count=0,
            false_negative_count=1,
            predicted_count=1,
            ground_truth_count=2,
            predicted_value_streams=["A"],
            ground_truth_value_streams=["A", "B"],
            true_positives=["A"],
            false_positives=[],
            false_negatives=["B"],
            selected_count=1,
            llm_candidate_count=2,
            merged_candidate_count=3,
            historical_hit_count=4,
            excluded_ticket_ids=["IDMT-1"],
            prompt_chars=100,
            system_prompt_chars=40,
            final_llm_ms=900,
        ),
        TicketMetrics(
            ticket_id="IDMT-2",
            file_name="IDMT-2.txt",
            status="ok",
            elapsed_seconds=3.0,
            vs_seconds=3.0,
            stage_seconds=0.0,
            total_seconds=3.0,
            precision=0.5,
            recall=1.0,
            f1=0.6667,
            true_positive_count=1,
            false_positive_count=1,
            false_negative_count=0,
            predicted_count=2,
            ground_truth_count=1,
            predicted_value_streams=["C", "D"],
            ground_truth_value_streams=["C"],
            true_positives=["C"],
            false_positives=["D"],
            false_negatives=[],
            selected_count=2,
            llm_candidate_count=2,
            merged_candidate_count=3,
            historical_hit_count=4,
            excluded_ticket_ids=["IDMT-2"],
            prompt_chars=300,
            system_prompt_chars=80,
            final_llm_ms=1100,
        ),
    ]

    summary = summarize(rows)

    assert summary["macro_precision"] == 0.75
    assert summary["macro_recall"] == 0.75
    assert summary["precision"] == 0.6667
    assert summary["recall"] == 0.6667
    assert summary["f1"] == 0.6667
    assert summary["micro_precision"] == 0.6667
    assert summary["micro_recall"] == 0.6667
    assert summary["avg_elapsed_seconds"] == 2.0
    assert summary["avg_vs_seconds"] == 2.0
    assert summary["avg_stage_seconds"] == 0.0
    assert summary["avg_total_seconds"] == 2.0
    assert summary["avg_prompt_chars"] == 200.0
    assert summary["avg_system_prompt_chars"] == 60.0
    assert summary["avg_llm_ms"] == 1000.0


def test_build_miss_bucket_summary_empty_results() -> None:
    summary = build_miss_bucket_summary([])

    assert summary == {
        "bucket_counts": {},
        "top_missed_gt_by_bucket": [],
        "worst_tickets_by_misses": [],
        "sent_to_llm_but_skipped_examples": [],
    }


def test_build_miss_bucket_summary_counts_multiple_buckets() -> None:
    summary = build_miss_bucket_summary(
        [
            {
                "ticket_id": "IDMT-1",
                "missed_ground_truth_debug": [
                    {
                        "loss_bucket": "sent_to_llm_but_skipped",
                        "ground_truth_name": "A",
                        "llm_rank": 8,
                        "merged_rank": 8,
                    },
                    {
                        "loss_bucket": "merged_not_sent_to_llm",
                        "ground_truth_name": "B",
                    },
                    {
                        "loss_bucket": "sent_to_llm_but_skipped",
                        "ground_truth_name": "A",
                        "llm_rank": 10,
                        "merged_rank": 10,
                    },
                ],
            }
        ]
    )

    assert summary["bucket_counts"] == {
        "sent_to_llm_but_skipped": 2,
        "merged_not_sent_to_llm": 1,
    }
    assert summary["top_missed_gt_by_bucket"][0] == {
        "bucket": "sent_to_llm_but_skipped",
        "ground_truth_name": "A",
        "count": 2,
    }
    assert summary["worst_tickets_by_misses"] == [
        {
            "ticket_id": "IDMT-1",
            "total_misses": 3,
            "buckets": {
                "sent_to_llm_but_skipped": 2,
                "merged_not_sent_to_llm": 1,
            },
        }
    ]


def test_build_miss_bucket_summary_sent_to_llm_examples_include_ranks() -> None:
    summary = build_miss_bucket_summary(
        [
            {
                "ticket_id": "IDMT-19761",
                "missed_ground_truth_debug": [
                    {
                        "loss_bucket": "sent_to_llm_but_skipped",
                        "ground_truth_name": "Issue Payment",
                        "llm_rank": 10,
                        "merged_rank": 7,
                    }
                ],
            }
        ]
    )

    assert summary["sent_to_llm_but_skipped_examples"] == [
        {
            "ticket_id": "IDMT-19761",
            "ground_truth_name": "Issue Payment",
            "llm_rank": 10,
            "merged_rank": 7,
        }
    ]


def test_eval_json_payload_includes_miss_bucket_summary(tmp_path, monkeypatch) -> None:
    idea_cards_dir = tmp_path / "idea_cards"
    output_dir = tmp_path / "out"
    idea_cards_dir.mkdir()
    item = EvaluationItem(
        ticket_id="IDMT-1",
        path=idea_cards_dir / "IDMT-1.txt",
        ground_truth=["A"],
    )
    item.path.write_text("idea", encoding="utf-8")
    missed = [
        {
            "loss_bucket": "sent_to_llm_but_skipped",
            "ground_truth_name": "A",
            "llm_rank": 3,
            "merged_rank": 2,
        }
    ]

    monkeypatch.setattr(eval_script, "load_ground_truth", lambda **kwargs: {"IDMT-1": ["A"]})
    monkeypatch.setattr(eval_script, "discover_items", lambda **kwargs: [item])
    monkeypatch.setattr(
        eval_script,
        "run_batch",
        lambda *args, **kwargs: [_ticket_metrics("IDMT-1", missed_ground_truth_debug=missed)],
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_rag_batch.py",
            "--idea-cards-dir",
            str(idea_cards_dir),
            "--output-dir",
            str(output_dir),
            "--no-shuffle",
        ],
    )

    assert main() == 0

    payload = eval_script.json.loads((output_dir / "rag_batch_eval.json").read_text(encoding="utf-8"))
    assert payload["miss_bucket_summary"]["bucket_counts"] == {
        "sent_to_llm_but_skipped": 1
    }
    assert payload["results"][0]["missed_ground_truth_debug"] == missed


def test_missed_ground_truth_debug_classifies_loss_bucket() -> None:
    payload = {
        "semantic_candidate_value_streams": [
            {"entity_id": "vs-a", "entity_name": "A", "semantic_rank": 2},
        ],
        "historical_value_stream_support": [
            {"entity_id": "vs-b", "entity_name": "B", "historical_rank": 4},
        ],
        "merged_candidate_value_streams": [
            {
                "entity_id": "vs-a",
                "entity_name": "A",
                "candidate_status": "sent_to_llm",
            },
            {
                "entity_id": "vs-b",
                "entity_name": "B",
                "candidate_status": "outside_llm_window",
            },
        ],
        "llm_candidates": [{"entity_id": "vs-a", "entity_name": "A"}],
    }

    debug = build_missed_ground_truth_debug(
        false_negatives=["A", "B", "C"],
        payload=payload,
        selected_rows=[],
    )

    assert [row["loss_bucket"] for row in debug] == [
        "sent_to_llm_but_skipped",
        "merged_not_sent_to_llm",
        "not_retrieved",
    ]
    assert debug[0]["semantic_rank"] == 2
    assert debug[1]["historical_rank"] == 4


def test_selected_same_id_miss_is_name_mismatch_bucket() -> None:
    assert (
        classify_ground_truth_miss(
            in_semantic=True,
            in_historical=False,
            in_merged=True,
            sent_to_llm=True,
            selected=False,
            selected_same_id=True,
        )
        == "selected_name_mismatch"
    )


def test_compute_metrics_uses_canonical_value_stream_names() -> None:
    metrics = compute_metrics(["Order to Cash"], ["Order to Cash for Group Coverage"])

    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["false_negatives"] == []


def test_load_ground_truth_from_azure_uses_value_stream_names(monkeypatch) -> None:
    monkeypatch.setattr(
        "vs_app.ingestion.persistence.azure_historical_index.load_historical_summary_rows",
        lambda **kwargs: [
            {"ticket_id": "idmt-1", "value_stream_names": ["Issue Payment", "Issue Payment"]},
            {"ticket_id": "IDMT-2", "direct_vs_names": ["Manage Member Care"]},
        ],
    )

    rows = load_ground_truth_from_azure(azure_index_name="hist")

    assert rows == {
        "IDMT-1": ["Issue Payment"],
        "IDMT-2": ["Manage Member Care"],
    }


def test_eval_defaults_and_transient_detector(monkeypatch) -> None:
    monkeypatch.delenv("CONDENSE_LLM_MODEL", raising=False)
    monkeypatch.delenv("GENERATION_LLM_REASONING_EFFORT", raising=False)

    apply_eval_llm_defaults()

    import os

    assert os.environ["CONDENSE_LLM_MODEL"] == "gpt-5-mini-idp"
    assert os.environ["GENERATION_LLM_REASONING_EFFORT"] == "medium"
    assert is_transient_gateway_error(RuntimeError("504 Gateway Time-out"))
    assert not is_transient_gateway_error(ValueError("bad local file"))
