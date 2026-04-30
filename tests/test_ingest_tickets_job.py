from pathlib import Path
import json
import shutil

from jobs.ingest_tickets import load_ticket_ids, resolve_ticket_ids


def test_resolve_ticket_ids_dedupes_uppercases_and_limits() -> None:
    assert resolve_ticket_ids(
        positional=["idmt-1", "IDMT-1", " idmt-2 "],
        input_ticket_ids=None,
        limit=1,
    ) == ["IDMT-1"]


def test_load_ticket_ids_from_text_and_json_files() -> None:
    tmp_dir = Path("pytest-cache-files-ingest-job-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        text_path = tmp_dir / "tickets.txt"
        text_path.write_text("IDMT-1, IDMT-2\n# comment\nIDMT-3\n", encoding="utf-8")
        assert load_ticket_ids(text_path) == ["IDMT-1", "IDMT-2", "IDMT-3"]

        json_path = tmp_dir / "tickets.json"
        json_path.write_text(json.dumps({"ticket_ids": ["IDMT-4", "IDMT-5"]}), encoding="utf-8")
        assert load_ticket_ids(json_path) == ["IDMT-4", "IDMT-5"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
