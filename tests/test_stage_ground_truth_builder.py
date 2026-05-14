from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

from vs_app.eval.stages.ground_truth_builder import (
    build_stage_ground_truth_for_tickets,
    fetch_theme_epic_rows,
    parse_stage_from_epic,
    parse_value_stream_from_theme,
    rows_to_ticket_ground_truth,
)


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_stage_ground_truth import load_ticket_keys


def _rows() -> list[dict]:
    return [
        {
            "ticket_id": "IDMT-19761",
            "ticket_summary": "CP 2026 Women's and Family Health",
            "theme_key": "GROUP-22223",
            "theme_summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program"
            ),
            "theme_issue_type": "Theme",
            "theme_business_value_streams": ["Manage Utilization Management Program"],
            "stage_issue_key": "GROUP-22805",
            "stage_summary": (
                "CP 2026 Women's and Family Health : Manage Utilization Management Program "
                "- Manage UM Operations (PA)"
            ),
            "stage_issue_type": "Epic",
            "stage_status": "Cancelled",
            "stage_resolution": "Won't Do",
        },
        {
            "ticket_id": "IDMT-19761",
            "ticket_summary": "CP 2026 Women's and Family Health",
            "theme_key": "GROUP-22223",
            "theme_summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program"
            ),
            "theme_issue_type": "Theme",
            "theme_business_value_streams": ["Manage Utilization Management Program"],
            "stage_issue_key": "GROUP-22838",
            "stage_summary": (
                "CP 2026 Women's and Family Health : Manage Utilization Management Program "
                "- Manage UM Operations (Referral)"
            ),
            "stage_issue_type": "Epic",
            "stage_status": "In Progress",
            "stage_resolution": None,
        },
    ]


def test_parse_value_stream_from_theme_prefers_business_value_streams() -> None:
    info = parse_value_stream_from_theme(
        ticket_summary="Ticket",
        theme_summary="Ticket : Issue Payment",
        theme_business_value_streams='["Manage Utilization Management Program"]',
    )

    assert info == {
        "raw": "Manage Utilization Management Program",
        "canonical": "Manage Utilization Management Program",
        "value_stream_id": "VSR00168130",
    }


def test_parse_stage_from_epic_removes_theme_prefix() -> None:
    assert (
        parse_stage_from_epic(
            theme_summary="Theme : Issue Payment",
            stage_summary="Theme : Issue Payment - Issue Refund",
        )
        == "Issue Refund"
    )


def test_rows_to_ticket_ground_truth_groups_stages_under_value_stream() -> None:
    result = rows_to_ticket_ground_truth(
        _rows(),
        stage_id_resolver=lambda **kwargs: (
            "VSS-PA" if kwargs["stage_name"] == "Manage UM Operations (PA)" else None
        ),
    )

    ticket = result["IDMT-19761"]
    assert ticket["source"] == "neo4j_jira_graph"
    assert len(ticket["value_streams"]) == 1
    value_stream = ticket["value_streams"][0]
    assert value_stream["theme_issue_key"] == "GROUP-22223"
    assert value_stream["value_stream_name_canonical"] == "Manage Utilization Management Program"
    assert value_stream["value_stream_id"] == "VSR00168130"
    assert [stage["stage_name_raw"] for stage in value_stream["stages"]] == [
        "Manage UM Operations (PA)",
        "Manage UM Operations (Referral)",
    ]
    assert value_stream["stages"][0]["stage_id"] == "VSS-PA"
    assert value_stream["stages"][0]["status"] == "Cancelled"


def test_rows_to_ticket_ground_truth_can_exclude_cancelled_epics() -> None:
    result = rows_to_ticket_ground_truth(
        _rows(),
        include_cancelled_epics=False,
        stage_id_resolver=lambda **kwargs: None,
    )

    stages = result["IDMT-19761"]["value_streams"][0]["stages"]
    assert [stage["stage_issue_key"] for stage in stages] == ["GROUP-22838"]


def test_rows_to_ticket_ground_truth_rejects_non_theme_group_rows() -> None:
    rows = _rows()
    rows[0]["theme_issue_type"] = "Epic"
    rows[1]["theme_issue_type"] = "Epic"

    assert rows_to_ticket_ground_truth(rows, stage_id_resolver=lambda **kwargs: None) == {}


class _FakeSession:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.seen_ticket_key = ""

    def run(self, query, **params):
        self.seen_ticket_key = params["ticket_key"]
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDriver:
    def __init__(self, rows: list[dict]) -> None:
        self.session_obj = _FakeSession(rows)

    def session(self):
        return self.session_obj


def test_fetch_theme_epic_rows_reads_neo4j_records() -> None:
    session = _FakeSession([{"ticket_id": "IDMT-1"}])

    rows = fetch_theme_epic_rows(neo4j_session=session, ticket_key="idmt-1")

    assert session.seen_ticket_key == "IDMT-1"
    assert rows == [{"ticket_id": "IDMT-1"}]


def test_build_stage_ground_truth_for_tickets_uses_driver_session() -> None:
    driver = _FakeDriver(_rows())

    result = build_stage_ground_truth_for_tickets(
        ticket_keys=["idmt-19761"],
        neo4j_driver=driver,
        stage_id_resolver=lambda **kwargs: None,
    )

    assert list(result) == ["IDMT-19761"]
    assert driver.session_obj.seen_ticket_key == "IDMT-19761"


def test_load_ticket_keys_reads_direct_json_and_csv() -> None:
    tmp_dir = Path("pytest-cache-files-stage-gt")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir()
    try:
        json_path = tmp_dir / "tickets.json"
        json_path.write_text(
            json.dumps({"results": [{"ticket_id": "IDMT-2"}, {"ticket_id": "idmt-1"}]}),
            encoding="utf-8",
        )
        args = argparse.Namespace(ticket=["IDMT-3"], tickets_file=str(json_path))
        assert load_ticket_keys(args) == ["IDMT-1", "IDMT-2", "IDMT-3"]

        csv_path = tmp_dir / "tickets.csv"
        csv_path.write_text("ticket_id\nidmt-5\nIDMT-4\n", encoding="utf-8")
        args = argparse.Namespace(ticket=[], tickets_file=str(csv_path))
        assert load_ticket_keys(args) == ["IDMT-4", "IDMT-5"]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

