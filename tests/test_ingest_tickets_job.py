from pathlib import Path
import json
import shutil

from jobs.ingest_tickets import ensure_value_stream_labels, load_ticket_ids, resolve_ticket_ids


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
        json_path.write_text(
            json.dumps({"ticket_ids": [{"ticket_id": "IDMT-4"}, {"key": "IDMT-5"}]}),
            encoding="utf-8",
        )
        assert load_ticket_ids(json_path) == ["IDMT-4", "IDMT-5"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_ensure_value_stream_labels_uses_issue_link_mapping() -> None:
    ticket_data = {
        "key": "IDMT-1",
        "fields": {
            "issuelinks": [
                {
                    "type": {"name": "Value Stream"},
                    "outwardIssue": {
                        "key": "GROUP-1",
                        "fields": {
                            "summary": "GROUP-1: Issue Payment",
                            "status": {"name": "Active"},
                        },
                    },
                }
            ]
        },
    }

    ensure_value_stream_labels(ticket_data)

    assert ticket_data["value_stream_ids"] == ["GROUP-1"]
    assert ticket_data["value_stream_names"] == ["Issue Payment"]
    assert ticket_data["value_stream_label_source"] == "jira_issuelinks"
