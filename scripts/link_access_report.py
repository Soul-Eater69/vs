"""
Link + attachment usefulness report for IDMT tickets.

Runs the full idea-card audit pipeline (fuzzy + LLM classification + link probing)
and flattens the result to a slim per-item view: every link and every attachment
with usefulness verdict and link accessibility status.

Usage:
  uv run python scripts/link_access_report.py \
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \
    --out-file ticket_data/link_access_report.json

  # Skip LLM (fuzzy classifier only — faster, no LLM cost):
  uv run python scripts/link_access_report.py \
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \
    --no-llm
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
from vs_app.jobs.jira_batch.runtime.runtime_factory import try_build_llm
from vs_app.modules.ingestion.idea_cards.models import (
    AttachmentCandidate,
    LinkAccessStatus,
    LinkCandidate,
    TicketAuditRecord,
)
from vs_app.modules.ingestion.idea_cards.service import audit_ticket

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
logging.getLogger("vs_app.modules.ingestion.value_stream_labels").setLevel(logging.ERROR)

_INACCESSIBLE_STATUSES = {
    LinkAccessStatus.auth_required,
    LinkAccessStatus.forbidden,
    LinkAccessStatus.sharepoint_auth_required,
    LinkAccessStatus.timeout,
    LinkAccessStatus.connection_error,
    LinkAccessStatus.not_found,
    LinkAccessStatus.unknown_error,
}
_DEFAULT_CONCURRENCY = int(os.environ.get("AUDIT_MAX_CONCURRENT", "10"))
_LLM_MODEL = os.environ.get("AUDIT_LLM_MODEL", "gpt-5-mini-idp")


def _final_useful(is_llm: Optional[bool], is_fuzzy: bool) -> bool:
    return is_llm if is_llm is not None else is_fuzzy


def _link_row(lnk: LinkCandidate) -> dict:
    return {
        "type": "link",
        "url": lnk.url,
        "host": lnk.url_host,
        "is_useful": _final_useful(lnk.is_useful_llm, lnk.is_useful_fuzzy),
        "is_useful_llm": lnk.is_useful_llm,
        "is_useful_fuzzy": lnk.is_useful_fuzzy,
        "useful_reason": lnk.usefulness_reason_llm or lnk.usefulness_reason_fuzzy,
        "access_status": lnk.access_status.value,
        "http_status": lnk.http_status,
        "mentioned_as_idea_card": lnk.mentioned_as_idea_card,
    }


def _att_row(att: AttachmentCandidate) -> dict:
    return {
        "type": "attachment",
        "filename": att.filename,
        "extension": att.extension,
        "size": att.size,
        "is_useful": _final_useful(att.is_useful_llm, att.is_useful_fuzzy),
        "is_useful_llm": att.is_useful_llm,
        "is_useful_fuzzy": att.is_useful_fuzzy,
        "useful_reason": att.usefulness_reason_llm or att.usefulness_reason_fuzzy,
        "mentioned_as_idea_card": att.mentioned_as_idea_card,
    }


def _summarize_record(record: TicketAuditRecord) -> dict:
    link_rows = [_link_row(l) for l in record.links]
    att_rows = [_att_row(a) for a in record.attachments]
    items = link_rows + att_rows

    inaccessible = sum(1 for l in record.links if l.access_status in _INACCESSIBLE_STATUSES)
    useful = sum(1 for i in items if i["is_useful"])

    return {
        "title": record.title,
        "totals": {
            "items": len(items),
            "links": len(link_rows),
            "attachments": len(att_rows),
            "useful_items": useful,
            "links_inaccessible": inaccessible,
        },
        "items": items,
    }


async def _audit_one(
    ticket_id: str,
    ticket_client,
    llm_client,
    sem: asyncio.Semaphore,
) -> tuple[str, Optional[dict], Optional[str]]:
    async with sem:
        try:
            record = await audit_ticket(
                ticket_id,
                ticket_client,
                probe_links=True,
                enable_llm=llm_client is not None,
                llm_every_ticket=llm_client is not None,
                llm_client=llm_client,
            )
        except Exception as exc:
            logger.warning("audit failed for %s: %s", ticket_id, exc)
            return ticket_id, None, str(exc)

    summary = _summarize_record(record)
    t = summary["totals"]
    logger.info(
        "[%s] items=%d (links=%d att=%d) useful=%d inaccessible=%d",
        ticket_id, t["items"], t["links"], t["attachments"],
        t["useful_items"], t["links_inaccessible"],
    )
    return ticket_id, summary, None


async def run(
    ticket_ids: list[str],
    *,
    source: str,
    out_file: Path,
    enable_llm: bool,
    concurrency: int,
) -> None:
    llm_client = try_build_llm(enable=True, model=_LLM_MODEL) if enable_llm else None
    if enable_llm and llm_client is None:
        logger.warning("LLM requested but client could not be built — falling back to fuzzy only")

    sem = asyncio.Semaphore(concurrency)
    logger.info("Auditing %d tickets (concurrency=%d, llm=%s)", len(ticket_ids), concurrency, llm_client is not None)

    async with build_ticket_fetcher(source=source, verify_ssl=False) as ticket_client:
        results = await asyncio.gather(
            *[_audit_one(tid, ticket_client, llm_client, sem) for tid in ticket_ids]
        )

    tickets: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for tid, summary, err in results:
        if err:
            errors[tid] = err
        elif summary is not None:
            tickets[tid] = summary

    overall = {
        "total_tickets": len(ticket_ids),
        "succeeded": len(tickets),
        "errors": len(errors),
        "total_links": sum(r["totals"]["links"] for r in tickets.values()),
        "total_attachments": sum(r["totals"]["attachments"] for r in tickets.values()),
        "total_items": sum(r["totals"]["items"] for r in tickets.values()),
        "total_useful_items": sum(r["totals"]["useful_items"] for r in tickets.values()),
        "total_links_inaccessible": sum(r["totals"]["links_inaccessible"] for r in tickets.values()),
        "tickets_with_useful": sum(1 for r in tickets.values() if r["totals"]["useful_items"] > 0),
    }

    output = {"overall": overall, "tickets": tickets, "errors": errors}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("-" * 60)
    print(f"Tickets processed   : {overall['succeeded']}/{overall['total_tickets']}")
    print(f"Total items         : {overall['total_items']} ({overall['total_links']} links + {overall['total_attachments']} attachments)")
    print(f"Useful items        : {overall['total_useful_items']}")
    print(f"Inaccessible links  : {overall['total_links_inaccessible']}")
    print(f"Tickets with useful : {overall['tickets_with_useful']}")
    print(f"Errors              : {overall['errors']}")
    print(f"Output              : {out_file}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Link + attachment usefulness/accessibility report.")
    parser.add_argument("--input-ticket-ids", required=True, metavar="JSON_FILE")
    parser.add_argument("--source", choices=["jira", "neo4j"], default="neo4j")
    parser.add_argument("--out-file", default="ticket_data/link_access_report.json")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM classification (fuzzy only).")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=_DEFAULT_CONCURRENCY)
    args = parser.parse_args()

    raw = json.loads(Path(args.input_ticket_ids).read_text(encoding="utf-8"))
    ids = raw if isinstance(raw, list) else (raw.get("ticket_ids") or [])
    ids = [str(t).strip().upper() for t in ids if t]
    if args.limit:
        ids = ids[: args.limit]

    if not ids:
        logger.error("No ticket IDs found in %s", args.input_ticket_ids)
        return

    await run(
        ids,
        source=args.source,
        out_file=Path(args.out_file),
        enable_llm=not args.no_llm,
        concurrency=args.concurrency,
    )


if __name__ == "__main__":
    asyncio.run(main())
