"""
Fetch a random sample of Jira tickets from Neo4j that have NO linked value
stream (no inward `GROUP-*` link resolving to an `issueType = "Theme"` node)
and were created on or after a chosen date (default 2023-01-01).

Usage:
  uv run python scripts/fetch_tickets_without_value_stream.py
  uv run python scripts/fetch_tickets_without_value_stream.py --limit 20 --since 2023-01-01
  uv run python scripts/fetch_tickets_without_value_stream.py --out tickets_no_vs.json

Env vars (loaded via vs_app.settings):
  NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from neo4j import GraphDatabase

from vs_app.settings import (
    NEO4J_AUTH,
    NEO4J_DATABASE,
    NEO4J_URI,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


CYPHER_RANDOM_TICKETS_NO_VS = """
MATCH (t:JIRA)
WHERE t.creationDateEpoch >= $since_epoch
  AND ($source_issue_type IS NULL OR t.issueType = $source_issue_type)
WITH
    t,
    [k IN coalesce(t.inwardIssues, []) WHERE k STARTS WITH "GROUP-"] AS group_keys

OPTIONAL MATCH (g:JIRA)
WHERE g.key IN group_keys AND g.issueType = $theme_issue_type
WITH t, group_keys, count(g) AS theme_count

WHERE theme_count = 0
WITH t, group_keys, rand() AS r
ORDER BY r
LIMIT $limit

RETURN
    t.key AS ticket_key,
    t.issueType AS issue_type,
    t.summary AS summary,
    t.creationDate AS creation_date,
    t.creationDateEpoch AS creation_epoch,
    group_keys AS linked_group_keys,
    coalesce(t.inwardIssues, []) AS inward_issues
"""


def fetch_random_tickets_without_value_stream(
    *,
    since_epoch: int,
    limit: int,
    source_issue_type: Optional[str],
    theme_issue_type: str,
) -> list[dict]:
    driver = GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
    try:
        driver.verify_connectivity()
        with driver.session(database=NEO4J_DATABASE) as session:
            result = session.run(
                CYPHER_RANDOM_TICKETS_NO_VS,
                {
                    "since_epoch": since_epoch,
                    "limit": limit,
                    "source_issue_type": source_issue_type,
                    "theme_issue_type": theme_issue_type,
                },
            )
            return [record.data() for record in result]
    finally:
        driver.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch random Neo4j tickets with no linked Theme value stream.",
    )
    parser.add_argument("--since", default="2023-01-01", help="ISO date (YYYY-MM-DD).")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--source-issue-type",
        default="Engagement Request",
        help='Optional filter, e.g. "Engagement Request". Default: any type.',
    )
    parser.add_argument("--theme-issue-type", default="Theme")
    parser.add_argument(
        "--out",
        type=Path,
        default="data/tickets_without_value_stream.json",
        help="Optional JSON output path. Prints to stdout if omitted.",
    )
    args = parser.parse_args()

    since_epoch = int(
        datetime.fromisoformat(args.since + "T00:00:00+00:00")
        .astimezone(timezone.utc)
        .timestamp()
    )
    logger.info(
        "Querying tickets without value stream — since=%s (epoch %d), limit=%d, source_type=%s",
        args.since,
        since_epoch,
        args.limit,
        args.source_issue_type or "ANY",
    )

    rows = fetch_random_tickets_without_value_stream(
        since_epoch=since_epoch,
        limit=args.limit,
        source_issue_type=args.source_issue_type,
        theme_issue_type=args.theme_issue_type,
    )

    logger.info("Fetched %d tickets with no value stream.", len(rows))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False, default=str)
        print(f"Wrote {len(rows)} tickets -> {args.out}")
    else:
        print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
