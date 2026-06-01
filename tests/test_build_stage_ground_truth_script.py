"""Tests for the build_stage_ground_truth script helpers.

Relocated from tests/test_stage_ground_truth_builder.py (Feature 7E) when the
orphaned eval Stage GT builder was retired. ``load_ticket_keys`` is a scripts/
helper unrelated to that builder and is only covered here.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_stage_ground_truth import load_ticket_keys


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
