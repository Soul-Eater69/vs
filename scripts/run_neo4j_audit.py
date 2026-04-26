"""
Metadata-only idea-card audit for Neo4j IDMT tickets.

Usage:
  uv run python scripts/run_neo4j_audit.py \
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \
    --out-file ticket_data/idea_card_audit/idea_card_audit_by_ticket.json \
    --probe-links \
    --enable-llm \
    --llm-every-ticket \
    --limit 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vs_app.container import build_ticket_fetcher
from vs_app.jobs.jira_batch.runtime.runtime_factory import try_build_llm
from vs_app.modules.ingestion.idea_cards.models import AuditRunMetadata, TicketAuditRecord
from vs_app.modules.ingestion.idea_cards.report import write_audit_output
from vs_app.modules.ingestion.idea_cards.service import audit_ticket

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

_DEFAULT_MAX_CONCURRENT = int(os.environ.get("AUDIT_MAX_CONCURRENT", "5"))
_LLM_MODEL = os.environ.get("AUDIT_LLM_MODEL", "gpt-5-mini-idp")


async def run_audit(
    ticket_ids: list[str],
    *,
    source: str = "neo4j",
    out_file: Path,
    probe_links: bool = True,
    enable_llm: bool = False,
    llm_every_ticket: bool = False,
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    limit: Optional[int] = None,
) -> None:
    if limit is not None:
        ticket_ids = ticket_ids[:limit]

    if not ticket_ids:
        logger.warning("No ticket IDs to process.")
        return

    llm_client = try_build_llm(enable=enable_llm, model=_LLM_MODEL) if enable_llm else None

    results: list[TicketAuditRecord] = []
    errors: list[dict] = []

    sem = asyncio.Semaphore(max_concurrent)

    async with build_ticket_fetcher(source=source, verify_ssl=False) as ticket_client:

        async def _audit_one(
            tid: str,
        ) -> tuple[str, Optional[TicketAuditRecord], Optional[str]]:
            async with sem:
                try:
                    result = await audit_ticket(
                        tid,
                        ticket_client,
                        probe_links=probe_links,
                        enable_llm=enable_llm,
                        llm_every_ticket=llm_every_ticket,
                        llm_client=llm_client,
                    )
                    return tid, result, None
                except Exception as exc:
                    logger.exception("Audit failed for %s", tid)
                    return tid, None, str(exc)

        gathered = await asyncio.gather(*[_audit_one(tid) for tid in ticket_ids])

    llm_called_count = 0
    for tid, result, err in gathered:
        if err:
            errors.append({"ticket_id": tid, "error": err})
            logger.error("[%s] ERROR: %s", tid, err)
            continue
        if result:
            results.append(result)
            fuzzy = result.fuzzy_decision
            llm = result.llm_decision
            cmp = result.comparison

            fuzzy_str = (
                f"{fuzzy.presence}({fuzzy.confidence:.2f}"
                + (f", {fuzzy.primary_source_id}" if fuzzy.primary_source_id else "")
                + ")"
            )
            if llm:
                llm_called_count += 1
                llm_str = (
                    f"{llm.presence}({llm.confidence:.2f}"
                    + (f", {llm.primary_source_id}" if llm.primary_source_id else "")
                    + ")"
                )
                compare_str = cmp.presence_match_type if cmp else "null"
                manual_str = str(result.final_recommendation.manual_review).lower()
                logger.info(
                    "[%s] links=%d useful_links=%d accessible_links=%d "
                    "fuzzy=%s llm=%s compare=%s manual_review=%s",
                    tid,
                    result.link_summary.total_links,
                    result.link_summary.useful_links,
                    result.link_summary.accessible_links,
                    fuzzy_str,
                    llm_str,
                    compare_str,
                    manual_str,
                )
            elif result.llm_error:
                logger.info(
                    "[%s] links=%d useful_links=%d accessible_links=%d "
                    "fuzzy=%s llm_error=%s comparison=null",
                    tid,
                    result.link_summary.total_links,
                    result.link_summary.useful_links,
                    result.link_summary.accessible_links,
                    fuzzy_str,
                    result.llm_error[:60],
                )
            else:
                logger.info(
                    "[%s] links=%d useful_links=%d accessible_links=%d fuzzy=%s llm=skipped",
                    tid,
                    result.link_summary.total_links,
                    result.link_summary.useful_links,
                    result.link_summary.accessible_links,
                    fuzzy_str,
                )

    run_metadata = AuditRunMetadata(
        source=source,
        total_tickets=len(ticket_ids),
        probe_links=probe_links,
        enable_llm=enable_llm,
        llm_every_ticket=llm_every_ticket,
        metadata_only=True,
        download_attachments=False,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    write_audit_output(results, out_file, run_metadata=run_metadata)

    if errors:
        err_path = out_file.parent / "_errors.json"
        with err_path.open("w", encoding="utf-8") as fh:
            json.dump(errors, fh, indent=2, ensure_ascii=False)

    print("-" * 60)
    print(f"AUDIT COMPLETE — {len(results)}/{len(ticket_ids)} succeeded")
    print(f"  LLM called        : {llm_called_count}")
    print(f"  Errors            : {len(errors)}")
    print(f"  Output file       : {out_file}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Metadata-only idea-card audit for IDMT Neo4j tickets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full audit with LLM on every ticket:
  uv run python scripts/run_neo4j_audit.py \\
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \\
    --out-file ticket_data/idea_card_audit/idea_card_audit_by_ticket.json \\
    --probe-links --enable-llm --llm-every-ticket --limit 20

  # Fast metadata-only, no LLM:
  uv run python scripts/run_neo4j_audit.py \\
    --input-ticket-ids data/valid_idmt_tickets_sample_20.json \\
    --out-file ticket_data/idea_card_audit/idea_card_audit_by_ticket.json
""",
    )
    parser.add_argument("--input-ticket-ids", required=True, metavar="JSON_FILE")
    parser.add_argument(
        "--source",
        choices=["jira", "neo4j"],
        default=os.environ.get("INGESTION_TICKET_SOURCE", "neo4j"),
    )
    parser.add_argument(
        "--out-file",
        default="ticket_data/idea_card_audit/idea_card_audit_by_ticket.json",
        metavar="FILE",
    )
    parser.add_argument("--probe-links", action="store_true", default=False)
    parser.add_argument("--enable-llm", action="store_true", default=False)
    parser.add_argument("--llm-every-ticket", action="store_true", default=False)
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=_DEFAULT_MAX_CONCURRENT,
        metavar="N",
    )

    args = parser.parse_args()

    input_path = Path(args.input_ticket_ids)
    with input_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    ticket_ids = data if isinstance(data, list) else (data.get("ticket_ids") or [])
    ticket_ids = [str(t).strip().upper() for t in ticket_ids if t]

    if not ticket_ids:
        logger.error("No ticket IDs found in %s", args.input_ticket_ids)
        return

    logger.info("Loaded %d ticket IDs from %s", len(ticket_ids), args.input_ticket_ids)

    await run_audit(
        ticket_ids,
        source=args.source,
        out_file=Path(args.out_file),
        probe_links=args.probe_links,
        enable_llm=args.enable_llm,
        llm_every_ticket=args.llm_every_ticket,
        max_concurrent=args.concurrency,
        limit=args.limit,
    )


if __name__ == "__main__":
    asyncio.run(main())
