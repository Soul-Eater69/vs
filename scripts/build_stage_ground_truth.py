"""Build stage ground truth from Neo4j Jira Theme -> Epic links.

The generated JSON is an evaluation artifact only. It is not used by prediction.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.eval.stages.ground_truth_builder import build_stage_ground_truth_for_tickets


def main() -> int:
    args = parse_args()
    ticket_keys = load_ticket_keys(args)
    if not ticket_keys:
        raise SystemExit("No ticket keys provided. Use --ticket or --tickets-file.")

    driver = make_neo4j_driver(
        uri=args.neo4j_uri,
        user=args.neo4j_user,
        password=args.neo4j_password,
    )
    with driver:
        ground_truth = build_stage_ground_truth_for_tickets(
            ticket_keys=ticket_keys,
            neo4j_driver=driver,
            include_cancelled_epics=not args.exclude_cancelled_epics,
        )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(ground_truth)} tickets to {output_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build stage ground truth from Neo4j Jira Theme -> Epic links.",
    )
    parser.add_argument("--tickets-file", help="JSON/CSV file with ticket IDs.")
    parser.add_argument(
        "--ticket",
        action="append",
        default=[],
        help="Ticket key; can be repeated.",
    )
    parser.add_argument(
        "--output",
        default="output/stage_eval/stage_ground_truth.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--include-cancelled-epics",
        action="store_true",
        default=True,
        help="Include cancelled Epic stage links. This is the default.",
    )
    parser.add_argument(
        "--exclude-cancelled-epics",
        action="store_true",
        help="Exclude Epic stage links whose Jira status is Cancelled.",
    )
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD"))
    return parser.parse_args()


def make_neo4j_driver(*, uri: str | None, user: str | None, password: str | None):
    if not uri or not user or not password:
        raise SystemExit(
            "Neo4j connection requires --neo4j-uri, --neo4j-user, and --neo4j-password "
            "or NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD."
        )
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise SystemExit(
            "The neo4j package is required to build stage ground truth. "
            "Install it in this environment before running the script."
        ) from exc
    return GraphDatabase.driver(uri, auth=(user, password))


def load_ticket_keys(args: argparse.Namespace) -> list[str]:
    keys = list(args.ticket or [])
    tickets_file = getattr(args, "tickets_file", None)

    if tickets_file:
        path = Path(tickets_file)
        if not path.exists():
            raise SystemExit(f"--tickets-file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".json":
            keys.extend(_ticket_keys_from_json(json.loads(path.read_text(encoding="utf-8"))))
        elif suffix == ".csv":
            keys.extend(_ticket_keys_from_csv(path))
        else:
            raise SystemExit("--tickets-file must be .json or .csv")

    return sorted({normalize_ticket_key(key) for key in keys if normalize_ticket_key(key)})


def _ticket_keys_from_json(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return _ticket_keys_from_sequence(payload)
    if isinstance(payload, dict):
        for key in ("tickets", "results", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return _ticket_keys_from_sequence(value)
        direct = payload.get("ticket_id") or payload.get("ticket_key") or payload.get("key")
        return [str(direct)] if direct else []
    return []


def _ticket_keys_from_sequence(values: list[Any]) -> list[str]:
    keys: list[str] = []
    for value in values:
        if isinstance(value, str):
            keys.append(value)
        elif isinstance(value, dict):
            key = value.get("ticket_id") or value.get("ticket_key") or value.get("key")
            if key:
                keys.append(str(key))
    return keys


def _ticket_keys_from_csv(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return []
        preferred = ("ticket_id", "ticket_key", "key")
        field = next((name for name in preferred if name in reader.fieldnames), reader.fieldnames[0])
        return [str(row.get(field) or "") for row in reader]


def normalize_ticket_key(value: Any) -> str:
    return str(value or "").strip().upper()


if __name__ == "__main__":
    raise SystemExit(main())

