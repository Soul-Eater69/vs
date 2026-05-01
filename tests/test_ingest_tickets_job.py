from pathlib import Path
import json
import shutil

from jobs.ingest_tickets import (
    ensure_value_stream_labels,
    load_summary_map,
    load_ticket_ids,
    resolve_ticket_ids,
    write_summary_aggregate,
)


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


def test_ensure_value_stream_labels_does_not_fallback_to_themes() -> None:
    ticket_data = {
        "key": "IDMT-1",
        "fields": {
            "issuelinks": [
                {
                    "type": {"outward": "implements"},
                    "outwardIssue": {
                        "key": "GROUP-1",
                        "fields": {
                            "summary": "GROUP-1: Issue Payment",
                            "issuetype": {"name": "Theme"},
                            "status": {"name": "Active"},
                        },
                    },
                }
            ]
        },
    }

    ensure_value_stream_labels(ticket_data)

    assert "value_stream_names" not in ticket_data
    assert "value_stream_ids" not in ticket_data


def test_summary_aggregate_loads_and_replaces_by_ticket_id() -> None:
    tmp_dir = Path("pytest-cache-files-ingest-job-test")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        aggregate = tmp_dir / "summaries.json"
        write_summary_aggregate(
            aggregate,
            [
                {"ticket_id": "IDMT-1", "summary_text": "old"},
                {"ticket_id": "IDMT-2", "summary_text": "keep"},
            ],
        )
        rows = load_summary_map(aggregate)
        rows["IDMT-1"] = {"ticket_id": "IDMT-1", "summary_text": "new"}
        write_summary_aggregate(aggregate, rows.values())

        reloaded = load_summary_map(aggregate)
        assert reloaded["IDMT-1"]["summary_text"] == "new"
        assert reloaded["IDMT-2"]["summary_text"] == "keep"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
