from pathlib import Path
import shutil
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_rag_batch import compute_metrics, discover_items, summarize


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
        )

        assert [item.ticket_id for item in items] == ["IDMT-1"]
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
        ),
        TicketMetrics(
            ticket_id="IDMT-2",
            file_name="IDMT-2.txt",
            status="ok",
            elapsed_seconds=3.0,
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
        ),
    ]

    summary = summarize(rows)

    assert summary["macro_precision"] == 0.75
    assert summary["macro_recall"] == 0.75
    assert summary["micro_precision"] == 0.6667
    assert summary["micro_recall"] == 0.6667
    assert summary["avg_elapsed_seconds"] == 2.0
