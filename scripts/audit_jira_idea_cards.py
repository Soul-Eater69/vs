"""Ask an LLM whether Jira ticket descriptions point to an idea card.

Examples:
  py -3 scripts/audit_jira_idea_cards.py --jql "project = IDMT ORDER BY updated DESC" --limit 200
  py -3 scripts/audit_jira_idea_cards.py --tickets IDMT-15181 IDMT-18437
  py -3 scripts/audit_jira_idea_cards.py --input-ticket-ids data/tickets.json --no-llm
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import re
from pathlib import Path
from typing import Any

from vs_app.container import build_ticket_fetcher
from vs_app.integrations.llm.client import complete_text
from vs_app.jobs.jira_batch.runtime.runtime_factory import try_build_llm
from vs_app.modules.prompts.loader import safe_json_extract
from vs_app.modules.tickets.text_processing import clean_jira_markup, extract_adf_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
DEFAULT_MODEL = "gpt-5-mini-idp"

SYSTEM_PROMPT = """You identify whether a Jira ticket description contains or points to an idea card.

An idea card may be named "idea card", or it may be a linked/attached deck, proposal,
business case, executive summary, initiative document, PPT/PDF/DOCX, or SharePoint link
that likely contains the idea-card content.

Return ONLY JSON:
{
  "has_idea_card": true|false,
  "confidence": 0.0-1.0,
  "link": "best idea-card URL or empty string",
  "source_text": "short phrase from the description that supports this",
  "reason": "one short reason"
}
"""


def _clean_ticket_ids(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        ticket_id = str(value or "").strip().upper()
        if ticket_id and ticket_id not in seen:
            seen.add(ticket_id)
            out.append(ticket_id)
    return out


def _read_ticket_ids(path: Path) -> list[str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return _clean_ticket_ids(raw)
    return _clean_ticket_ids(raw.get("ticket_ids") or raw.get("tickets") or [])


def _description_to_text(description: Any) -> str:
    if isinstance(description, (dict, list)):
        return clean_jira_markup(extract_adf_text(description))
    return clean_jira_markup(str(description or ""))


def _urls(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:!?)")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _looks_like_sharepoint(url: str) -> bool:
    host = url.lower()
    return "sharepoint.com" in host or "sharepoint-df.com" in host or "my.sharepoint" in host


def _heuristic(text: str, urls: list[str]) -> dict[str, Any]:
    lowered = text.lower()
    has_phrase = any(
        phrase in lowered
        for phrase in (
            "idea card",
            "business case",
            "executive summary",
            "proposal",
            "deck",
            "pptx",
            "powerpoint",
        )
    )
    likely_urls = [
        url
        for url in urls
        if any(token in url.lower() for token in ("idea", "card", "ppt", "pdf", "doc", "sharepoint"))
    ]
    return {
        "has_idea_card": bool(has_phrase or likely_urls),
        "confidence": 0.65 if has_phrase or likely_urls else 0.25,
        "link": likely_urls[0] if likely_urls else "",
        "source_text": "",
        "reason": "Heuristic match only; LLM disabled.",
    }


def _ask_llm(llm_client: Any, ticket_id: str, summary: str, description: str, urls: list[str]) -> dict[str, Any]:
    prompt = f"""Ticket: {ticket_id}
Summary: {summary}

URLs found in description:
{json.dumps(urls, indent=2)}

Description:
{description[:8000]}
"""
    raw = complete_text(
        prompt=prompt,
        llm_client=llm_client,
        system_prompt=SYSTEM_PROMPT,
        max_output_tokens=500,
        temperature=0.0,
    )
    parsed = safe_json_extract(raw)
    if not parsed:
        return {
            "has_idea_card": False,
            "confidence": 0.0,
            "link": "",
            "source_text": "",
            "reason": f"Could not parse LLM response: {raw[:200]}",
        }
    return parsed


async def _ids_from_jql(ticket_client: Any, jql: str, limit: int | None, page_size: int) -> list[str]:
    ids: list[str] = []
    start_at = 0
    while limit is None or len(ids) < limit:
        max_results = page_size if limit is None else min(page_size, limit - len(ids))
        payload = await ticket_client.search_issues(jql, start_at=start_at, max_results=max_results)
        issues = payload.get("issues") or []
        ids.extend(str(issue.get("key") or "").upper() for issue in issues if issue.get("key"))
        logger.info("Jira search fetched %d tickets (%d total so far)", len(issues), len(ids))
        if not issues:
            break
        total = payload.get("total")
        start_at += len(issues)
        if total is not None and start_at >= int(total):
            break
    return _clean_ticket_ids(ids)


async def _fetch_description(ticket_client: Any, ticket_id: str) -> tuple[str, str]:
    issue = await ticket_client.client.get_issue_by_key(ticket_id, fields=["summary", "description"])
    fields = issue.get("fields") or {}
    return str(fields.get("summary") or ""), _description_to_text(fields.get("description"))


async def run(args: argparse.Namespace) -> None:
    llm_client = try_build_llm(enable=not args.no_llm, model=args.llm_model)
    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    async with build_ticket_fetcher(source="jira", verify_ssl=args.verify_ssl) as ticket_client:
        ticket_ids = _clean_ticket_ids(args.tickets)
        if args.input_ticket_ids:
            ticket_ids.extend(_read_ticket_ids(Path(args.input_ticket_ids)))
            ticket_ids = _clean_ticket_ids(ticket_ids)
        if args.jql:
            ticket_ids.extend(await _ids_from_jql(ticket_client, args.jql, args.limit, args.page_size))
            ticket_ids = _clean_ticket_ids(ticket_ids)
        if args.limit:
            ticket_ids = ticket_ids[: args.limit]
        if not ticket_ids:
            raise SystemExit("Pass --jql, --tickets, or --input-ticket-ids.")

        for ticket_id in ticket_ids:
            try:
                summary, description = await _fetch_description(ticket_client, ticket_id)
                found_urls = _urls(description)
                verdict = (
                    _heuristic(description, found_urls)
                    if llm_client is None
                    else await asyncio.to_thread(_ask_llm, llm_client, ticket_id, summary, description, found_urls)
                )
                row = {
                    "ticket_id": ticket_id,
                    "summary": summary,
                    "has_idea_card": bool(verdict.get("has_idea_card")),
                    "confidence": verdict.get("confidence", 0),
                    "idea_card_link": verdict.get("link", "") or "",
                    "is_sharepoint_link": _looks_like_sharepoint(str(verdict.get("link", ""))),
                    "all_description_links": " | ".join(found_urls),
                    "source_text": verdict.get("source_text", "") or "",
                    "reason": verdict.get("reason", "") or "",
                }
                rows.append(row)
                logger.info("[%s] idea_card=%s link=%s", ticket_id, row["has_idea_card"], row["idea_card_link"])
            except Exception as exc:
                errors[ticket_id] = str(exc)
                logger.warning("[%s] failed: %s", ticket_id, exc)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "jira_idea_card_description_audit.csv"
    json_path = out_dir / "jira_idea_card_description_audit.json"

    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    output = {
        "summary": {
            "tickets": len(rows),
            "errors": len(errors),
            "with_idea_card": sum(1 for row in rows if row["has_idea_card"]),
            "with_sharepoint_idea_card_link": sum(1 for row in rows if row["is_sharepoint_link"]),
        },
        "tickets": rows,
        "errors": errors,
    }
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Tickets checked: {len(rows)}")
    print(f"With idea card: {output['summary']['with_idea_card']}")
    print(f"With SharePoint idea-card link: {output['summary']['with_sharepoint_idea_card_link']}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple Jira description idea-card audit.")
    parser.add_argument("--jql")
    parser.add_argument("--tickets", nargs="*", default=[])
    parser.add_argument("--input-ticket-ids")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--out-dir", default="output/idea_card_audit")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--llm-model", default=DEFAULT_MODEL)
    parser.add_argument("--verify-ssl", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
