"""
Quick link accessibility report for IDMT tickets.

Fetches tickets from Neo4j, extracts all description links, probes each one,
runs fuzzy scoring, and outputs a focused JSON of useful vs accessible links.

Usage:
  uv run python scripts/link_access_report.py \
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \
    --out-file ticket_data/link_access_report.json

  # Skip probing (faster, no network calls):
  uv run python scripts/link_access_report.py \
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \
    --no-probe
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from vs_app.container import build_ticket_fetcher
from vs_app.modules.ingestion.idea_cards.accessibility import probe_link
from vs_app.modules.ingestion.idea_cards.deterministic_classifier import classify_fuzzy
from vs_app.modules.ingestion.idea_cards.link_extractor import extract_description_candidates
from vs_app.modules.ingestion.idea_cards.models import LinkAccessStatus

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("vs_app.modules.ingestion.value_stream_labels").setLevel(logging.ERROR)

_INACCESSIBLE = {
    "auth_required", "forbidden", "sharepoint_auth_required",
    "timeout", "connection_error", "not_found", "unknown_error",
}
# Semaphore only guards Neo4j fetches — link probing is pure HTTP and runs
# fully parallel across all tickets after the fetch phase.
_DEFAULT_FETCH_CONCURRENCY = int(os.environ.get("AUDIT_MAX_CONCURRENT", "20"))


def _link_row(lnk) -> dict:
    status = lnk.access_status.value if isinstance(lnk.access_status, LinkAccessStatus) else str(lnk.access_status)
    return {
        "url": lnk.url,
        "host": lnk.url_host,
        "is_useful": lnk.is_useful_fuzzy,
        "usefulness_reason": lnk.usefulness_reason_fuzzy,
        "access_status": status,
        "http_status": lnk.http_status,
        "mentioned_as_idea_card": lnk.mentioned_as_idea_card,
    }


async def _fetch_ticket(
    ticket_id: str,
    ticket_client,
    sem: asyncio.Semaphore,
) -> tuple[str, Optional[dict], Optional[str]]:
    """Phase 1: fetch from Neo4j and extract links (rate-limited)."""
    async with sem:
        try:
            ticket_data = await ticket_client.get_ticket_data(ticket_id)
            fields = ticket_data.get("fields") or {}
            logger.info("fetched %s", ticket_id)
            return ticket_id, {
                "title": str(fields.get("summary") or ticket_id),
                "description": str(fields.get("description") or ""),
            }, None
        except Exception as exc:
            logger.warning("fetch failed %s: %s", ticket_id, exc)
            return ticket_id, None, str(exc)


async def _probe_and_score(
    ticket_id: str,
    title: str,
    description: str,
    probe_links: bool,
) -> tuple[str, Optional[dict], Optional[str]]:
    """Phase 2: extract links, probe, score — fully parallel, no semaphore."""
    try:
        links, statements = extract_description_candidates(description)

        if probe_links and links:
            links = list(await asyncio.gather(*[probe_link(lnk) for lnk in links]))

        classify_fuzzy(statements, links, attachments=[])

        useful = [lnk for lnk in links if lnk.is_useful_fuzzy]
        useful_accessible = [lnk for lnk in useful if lnk.access_status == LinkAccessStatus.accessible]
        useful_inaccessible = [lnk for lnk in useful if lnk.access_status.value in _INACCESSIBLE]

        return ticket_id, {
            "title": title,
            "total_links": len(links),
            "useful_links": len(useful),
            "useful_accessible": len(useful_accessible),
            "useful_inaccessible": len(useful_inaccessible),
            "links": [_link_row(lnk) for lnk in links],
        }, None
    except Exception as exc:
        return ticket_id, None, str(exc)


async def run(
    ticket_ids: list[str],
    *,
    source: str,
    out_file: Path,
    probe_links: bool,
    fetch_concurrency: int,
) -> None:
    sem = asyncio.Semaphore(fetch_concurrency)

    # Phase 1: fetch all tickets from Neo4j (rate-limited)
    logger.info("Fetching %d tickets (concurrency=%d)...", len(ticket_ids), fetch_concurrency)
    async with build_ticket_fetcher(source=source, verify_ssl=False) as client:
        fetched = await asyncio.gather(
            *[_fetch_ticket(tid, client, sem) for tid in ticket_ids]
        )

    # Phase 2: probe + score all tickets in parallel (no bottleneck)
    logger.info("Probing links%s...", "" if probe_links else " (skipped)")

    async def _passthrough_error(tid: str, err: str):
        return tid, None, err

    scored = await asyncio.gather(
        *[
            _probe_and_score(tid, data["title"], data["description"], probe_links)
            if data else _passthrough_error(tid, err)
            for tid, data, err in fetched
        ]
    )

    tickets: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for tid, record, err in scored:
        if err:
            errors[tid] = err
        else:
            tickets[tid] = record
            logger.info("[%s] total=%d useful=%d accessible=%d inaccessible=%d",
                        tid, record["total_links"], record["useful_links"],
                        record["useful_accessible"], record["useful_inaccessible"])

    all_useful = sum(r["useful_links"] for r in tickets.values())
    all_useful_acc = sum(r["useful_accessible"] for r in tickets.values())
    all_useful_inacc = sum(r["useful_inaccessible"] for r in tickets.values())
    tickets_with_useful = sum(1 for r in tickets.values() if r["useful_links"] > 0)

    output = {
        "overall": {
            "total_tickets": len(ticket_ids),
            "succeeded": len(tickets),
            "errors": len(errors),
            "tickets_with_useful_links": tickets_with_useful,
            "total_useful_links": all_useful,
            "useful_accessible": all_useful_acc,
            "useful_inaccessible": all_useful_inacc,
        },
        "tickets": tickets,
        "errors": errors,
    }

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("-" * 50)
    print(f"Tickets processed : {len(tickets)}/{len(ticket_ids)}")
    print(f"Useful links      : {all_useful}")
    print(f"  accessible      : {all_useful_acc}")
    print(f"  inaccessible    : {all_useful_inacc}")
    print(f"Output            : {out_file}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Link accessibility report for IDMT tickets.")
    parser.add_argument("--input-ticket-ids", required=True, metavar="JSON_FILE")
    parser.add_argument("--source", choices=["jira", "neo4j"], default="neo4j")
    parser.add_argument("--out-file", default="ticket_data/link_access_report.json")
    parser.add_argument("--no-probe", action="store_true", help="Skip HTTP probing.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=_DEFAULT_FETCH_CONCURRENCY,
                        help=f"Neo4j fetch concurrency (default: {_DEFAULT_FETCH_CONCURRENCY}).")
    args = parser.parse_args()

    raw = json.loads(Path(args.input_ticket_ids).read_text(encoding="utf-8"))
    ids = raw if isinstance(raw, list) else (raw.get("ticket_ids") or [])
    ids = [str(t).strip().upper() for t in ids if t]
    if args.limit:
        ids = ids[: args.limit]

    if not ids:
        logger.error("No ticket IDs found in %s", args.input_ticket_ids)
        return

    logger.info("Processing %d tickets", len(ids))
    await run(
        ids,
        source=args.source,
        out_file=Path(args.out_file),
        probe_links=not args.no_probe,
        fetch_concurrency=args.concurrency,
    )


if __name__ == "__main__":
    asyncio.run(main())
