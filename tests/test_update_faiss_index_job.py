from pathlib import Path
import json
import shutil

from jobs.update_faiss_index import count_chunk_artifacts, count_summary_artifacts


def test_counts_aggregate_summary_and_chunk_artifacts() -> None:
    tmp_dir = Path("pytest-cache-files-faiss-job-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        (tmp_dir / "summaries.json").write_text(
            json.dumps({"summaries": [{"ticket_id": "IDMT-1"}, {"ticket_id": "IDMT-2"}]}),
            encoding="utf-8",
        )
        (tmp_dir / "_all_chunks.json").write_text(
            json.dumps({"chunks": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}),
            encoding="utf-8",
        )

        assert count_summary_artifacts(tmp_dir) == 2
        assert count_chunk_artifacts(tmp_dir) == 3
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_counts_per_ticket_summary_and_chunk_artifacts() -> None:
    tmp_dir = Path("pytest-cache-files-faiss-job-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        ticket_dir = tmp_dir / "IDMT-1"
        ticket_dir.mkdir(parents=True)
        (ticket_dir / "summary.json").write_text(
            json.dumps({"ticket_id": "IDMT-1"}),
            encoding="utf-8",
        )
        (ticket_dir / "chunks.json").write_text(
            json.dumps({"documents": [{"id": "leaf"}], "sections": [{"id": "section"}]}),
            encoding="utf-8",
        )

        assert count_summary_artifacts(tmp_dir) == 1
        assert count_chunk_artifacts(tmp_dir) == 2
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
