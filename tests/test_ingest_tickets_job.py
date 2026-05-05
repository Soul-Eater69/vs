from pathlib import Path
import json
import shutil
import sys

import pytest

from jobs.ingest_tickets import (
    ensure_value_stream_labels,
    ingest_one_ticket,
    load_summary_map,
    load_ticket_ids,
    main,
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


def test_no_llm_exits_early(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["ingest_tickets.py", "IDMT-1", "--no-llm"])

    with pytest.raises(SystemExit, match="--no-llm is no longer supported"):
        main()


class _FakeJiraClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def get_ticket_data(self, ticket_id: str, config=None, llm_client=None):
        return dict(self.payload)


@pytest.mark.anyio
async def test_ingest_one_ticket_missing_official_labels_raises(capsys) -> None:
    with pytest.raises(RuntimeError, match="refusing to index unlabeled ticket"):
        await ingest_one_ticket(
            ticket_id="IDMT-1",
            jira_client=_FakeJiraClient({"key": "IDMT-1", "fields": {"issuelinks": []}}),
            llm_client=object(),
            embedding_client=None,
            cfg=object(),
        )

    captured = capsys.readouterr()
    assert "[IDMT-1] ERROR no official Jira Theme value-stream labels" in captured.out


@pytest.mark.anyio
async def test_ingest_one_ticket_prints_high_level_progress(monkeypatch, capsys) -> None:
    class FakeResult:
        def to_index_doc(self):
            return {
                "ticket_id": "IDMT-1",
                "value_stream_names": ["Issue Payment"],
                "direct_vs_names": ["Issue Payment"],
                "implied_vs_names": [],
                "summary_embedding": [0.1, 0.2, 0.3],
            }

    async def fake_ingest_ticket_summary_payload(**kwargs):
        kwargs["progress"]("ATTACHMENT accepted 1/1: Idea Card.pdf words=40 chars=279")
        return FakeResult()

    monkeypatch.setattr(
        "jobs.ingest_tickets.ingest_ticket_summary_payload",
        fake_ingest_ticket_summary_payload,
    )

    result = await ingest_one_ticket(
        ticket_id="IDMT-1",
        jira_client=_FakeJiraClient(
            {
                "key": "IDMT-1",
                "fields": {},
                "value_stream_names": ["Issue Payment"],
                "value_stream_ids": ["GROUP-1"],
            }
        ),
        llm_client=object(),
        embedding_client=object(),
        cfg=object(),
    )

    captured = capsys.readouterr()
    assert result["value_stream_names"] == ["Issue Payment"]
    assert "[IDMT-1] FETCHING Jira payload" in captured.out
    assert "[IDMT-1] FETCHED Jira payload" in captured.out
    assert "[IDMT-1] VS labels: 1 names" in captured.out
    assert "[IDMT-1]   - Issue Payment" in captured.out
    assert "[IDMT-1] SUMMARY/CLASSIFICATION started" in captured.out
    assert "[IDMT-1] ATTACHMENT accepted 1/1: Idea Card.pdf words=40 chars=279" in captured.out
    assert "[IDMT-1] CLASSIFICATION direct=1 implied=0" in captured.out
    assert "[IDMT-1] EMBEDDING complete dim=3" in captured.out
    assert "[IDMT-1] COMPLETE" in captured.out
