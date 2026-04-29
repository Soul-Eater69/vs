"""Ask an LLM whether Jira ticket descriptions point to an idea card.

Examples:
  py -3 scripts/audit_jira_idea_cards.py --jql "project = IDMT ORDER BY updated DESC" --limit 200
  py -3 scripts/audit_jira_idea_cards.py --tickets IDMT-15181 IDMT-18437
  py -3 scripts/audit_jira_idea_cards.py --input-ticket-ids data/tickets.json --no-llm
  py -3 scripts/audit_jira_idea_cards.py --jql "project = IDMT" --concurrency 10
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from vs_app.container import build_ticket_fetcher
from vs_app.integrations.llm.client import complete_text
from vs_app.jobs.jira_batch.runtime.runtime_factory import try_build_llm
from vs_app.modules.prompts.loader import safe_json_extract
from vs_app.modules.tickets.text_processing import clean_jira_markup, extract_adf_text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
DEFAULT_MODEL = "gpt-5-mini-idp"
DEFAULT_CONCURRENCY = int(os.environ.get("AUDIT_MAX_CONCURRENT", "5"))

SYSTEM_PROMPT = """You identify whether a Jira ticket description contains or points to an idea card.

An idea card may be named "idea card", or it may be a linked/attached deck, proposal,
business case, executive summary, initiative document, PPT/PDF/DOCX, or SharePoint link
that likely contains the idea-card content.

Important link rules:
- If the description says the idea card is in the attachment section, attached, or "see attachment",
  then has_idea_card=true. If there is also an explicit idea-card URL in description, keep that URL
  with source_location="description_link"; otherwise source_location="attachment_section" and link="".
- Do NOT use a nearby Gate 0 estimate, estimate workbook, SEW, architecture, bill of materials,
  CDD, Excel, xls, xlsx, or xlsm link as the idea-card link.
- Only return a link when the URL itself is explicitly on the idea-card/intake-form line or clearly
  labeled as the idea card link.

Return ONLY JSON:
{
  "has_idea_card": true|false,
  "confidence": 0.0-1.0,
  "link": "best idea-card URL or empty string",
  "source_location": "description_link|attachment_section|description_text|none",
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


def _idea_card_attachment_statement(text: str) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if "idea card" not in lowered and "intake form" not in lowered:
            continue
        if any(marker in lowered for marker in ("see attachment", "attachment section", "attached")):
            return line.strip()
    return ""


def _line_for_url(text: str, url: str) -> str:
    if not url:
        return ""
    for line in text.splitlines():
        if url in line:
            return line.strip()
    return ""


def _is_bad_neighbor_link(line: str, url: str) -> bool:
    lowered_line = line.lower()
    cleaned_line = URL_RE.sub("", lowered_line)
    parsed = urlparse(url)
    filename = unquote(parsed.path.rsplit("/", 1)[-1]).lower()
    bad_line_markers = (
        "gate 0",
        "estimate",
        "workbook",
        "sew",
        "architecture",
        "bill of materials",
        "cdd",
    )
    bad_file_markers = (".xls", ".xlsx", ".xlsm", "estimate", "workbook", "gate0", "gate-0")
    return any(marker in cleaned_line for marker in bad_line_markers) or any(
        marker in filename for marker in bad_file_markers
    )


def _has_idea_card_label(text: str) -> bool:
    lowered = text.lower()
    return (
        "idea card" in lowered
        or "intake form" in lowered
        or "business case" in lowered
        or "executive summary" in lowered
    )


def _valid_idea_card_link(text: str, url: str, source_text: str = "") -> bool:
    if not url:
        return False
    line = _line_for_url(text, url)
    if _is_bad_neighbor_link(f"{line}\n{source_text}", url):
        return False
    if _has_idea_card_label(line):
        return True
    return bool(source_text and url in text and _has_idea_card_label(source_text))


def _extension_rank(url: str) -> int:
    path = unquote(urlparse(url).path).lower()
    preferred = (".pptx", ".ppt", ".pdf", ".docx", ".doc", ".pptm")
    for idx, ext in enumerate(preferred):
        if path.endswith(ext):
            return idx
    return len(preferred)


def _candidate_idea_card_urls(text: str, source_text: str = "") -> list[str]:
    urls = _urls(text)
    strict = [url for url in urls if _valid_idea_card_link(text, url, source_text)]
    if strict:
        return strict

    # Jira rendering can separate label text and raw URL across lines; keep a relaxed fallback.
    if not _has_idea_card_label(text) and not _has_idea_card_label(source_text):
        return []

    relaxed = [url for url in urls if not _is_bad_neighbor_link(source_text, url)]
    return sorted(relaxed, key=_extension_rank)


def _normalize_verdict(verdict: dict[str, Any], description: str) -> dict[str, Any]:
    verdict = dict(verdict)

    link = str(verdict.get("link") or "").strip()
    source_text = str(verdict.get("source_text") or "")
    attachment_statement = _idea_card_attachment_statement(description)

    normalized_link = ""
    normalized_reason = ""
    if link and _valid_idea_card_link(description, link, source_text):
        normalized_link = link
    else:
        # LLM sometimes returns a transformed SharePoint URL; remap to a URL we actually extracted.
        candidate_urls = _candidate_idea_card_urls(description, source_text)
        if candidate_urls:
            normalized_link = candidate_urls[0]
            normalized_reason = "Mapped labeled idea-card text to extracted description URL."

    if attachment_statement:
        verdict["has_idea_card"] = True
        verdict["confidence"] = max(float(verdict.get("confidence") or 0), 0.9)
        if normalized_link:
            verdict["link"] = normalized_link
            verdict["source_location"] = "description_link"
            verdict["source_text"] = source_text or attachment_statement
            verdict["reason"] = (
                "Description references attachments and also includes an explicit idea-card link."
                if not normalized_reason
                else normalized_reason
            )
        else:
            verdict["link"] = ""
            verdict["source_location"] = "attachment_section"
            verdict["source_text"] = attachment_statement
            verdict["reason"] = "Description says the idea card is in the attachment section; no direct description link."
        return verdict

    if normalized_link:
        verdict["link"] = normalized_link
        verdict["source_location"] = "description_link"
        if normalized_reason:
            verdict["reason"] = normalized_reason
    else:
        verdict["link"] = ""
        verdict["source_location"] = verdict.get("source_location") or (
            "description_text" if verdict.get("has_idea_card") else "none"
        )
        if verdict.get("has_idea_card"):
            verdict["reason"] = verdict.get("reason") or "Idea card is mentioned, but no explicit idea-card URL was validated."
    return verdict


def _heuristic(text: str, urls: list[str]) -> dict[str, Any]:
    attachment_statement = _idea_card_attachment_statement(text)
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
    likely_urls = _candidate_idea_card_urls(text)
    if attachment_statement:
        if likely_urls:
            return {
                "has_idea_card": True,
                "confidence": 0.9,
                "link": likely_urls[0],
                "source_location": "description_link",
                "source_text": attachment_statement,
                "reason": "Description references attachments and includes an explicit idea-card link.",
            }
        return {
            "has_idea_card": True,
            "confidence": 0.9,
            "link": "",
            "source_location": "attachment_section",
            "source_text": attachment_statement,
            "reason": "Description says the idea card is in the attachment section.",
        }

    return {
        "has_idea_card": bool(has_phrase or likely_urls),
        "confidence": 0.65 if has_phrase or likely_urls else 0.25,
        "link": likely_urls[0] if likely_urls else "",
        "source_location": "description_link" if likely_urls else ("description_text" if has_phrase else "none"),
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
            "source_location": "none",
            "source_text": "",
            "reason": f"Could not parse LLM response: {raw[:200]}",
        }
    return _normalize_verdict(parsed, description)


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


async def _audit_one_ticket(
    ticket_client: Any,
    llm_client: Any,
    sem: asyncio.Semaphore,
    ticket_id: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    async with sem:
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
                "source_location": verdict.get("source_location", "") or "",
                "is_sharepoint_link": _looks_like_sharepoint(str(verdict.get("link", ""))),
                "all_description_links": " | ".join(found_urls),
                "source_text": verdict.get("source_text", "") or "",
                "reason": verdict.get("reason", "") or "",
            }
            logger.info("[%s] idea_card=%s link=%s", ticket_id, row["has_idea_card"], row["idea_card_link"])
            return ticket_id, row, None
        except Exception as exc:
            logger.warning("[%s] failed: %s", ticket_id, exc)
            return ticket_id, None, str(exc)


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

        concurrency = max(1, args.concurrency)
        logger.info("Auditing %d tickets (concurrency=%d, llm=%s)", len(ticket_ids), concurrency, llm_client is not None)
        sem = asyncio.Semaphore(concurrency)
        gathered = await asyncio.gather(
            *[_audit_one_ticket(ticket_client, llm_client, sem, ticket_id) for ticket_id in ticket_ids]
        )

        for ticket_id, row, error in gathered:
            if error:
                errors[ticket_id] = error
            elif row is not None:
                rows.append(row)

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
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
