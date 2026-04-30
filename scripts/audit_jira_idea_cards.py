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
TOKEN_RE = re.compile(r"[a-z0-9]+")
FILE_IN_TEXT_RE = re.compile(r"([A-Za-z0-9 _().-]+\.(?:pptx|ppt|pdf|docx|doc|pptm|xlsx|xlsm|xls))", re.IGNORECASE)
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


def _looks_like_portal_or_proxy(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path = unquote(parsed.path or "").lower()
    query = (parsed.query or "").lower()
    if "urldefense.com" in host:
        return True
    if "servicenow" in host:
        return True
    if any(marker in query for marker in ("table=", "sys_id=", "id=ticket", "tab=")):
        return True
    if not any(path.endswith(ext) for ext in (".pptx", ".ppt", ".pdf", ".docx", ".doc", ".pptm", ".xlsx", ".xlsm", ".xls")):
        if any(marker in path for marker in ("/sp", "/nav_to.do", "/browse/")):
            return True
    return False


def _has_clear_file_reference(verdict: dict[str, Any]) -> bool:
    link = str(verdict.get("link") or "").strip()
    if link:
        return True
    return str(verdict.get("source_location") or "") == "attachment_section"


def _clear_file_location(verdict: dict[str, Any]) -> str:
    link = str(verdict.get("link") or "").strip()
    if link:
        return "description_link"
    if str(verdict.get("source_location") or "") == "attachment_section":
        return "attachment_section"
    return "none"


def _idea_card_attachment_statement(text: str) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if "idea card" not in lowered and "intake form" not in lowered:
            continue
        if any(marker in lowered for marker in ("see attachment", "attachment section", "attached")):
            return line.strip()
    return ""


def _extract_attachments(fields: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in fields.get("attachment") or []:
        filename = str(item.get("filename") or "").strip()
        url = str(item.get("content") or item.get("self") or "").strip()
        mime_type = str(item.get("mimeType") or "").strip()
        if filename or url:
            out.append({"filename": filename, "url": url, "mime_type": mime_type})
    return out


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


def _filename_extension_rank(filename: str) -> int:
    lowered = filename.lower()
    preferred = (".pptx", ".ppt", ".pdf", ".docx", ".doc", ".pptm")
    for idx, ext in enumerate(preferred):
        if lowered.endswith(ext):
            return idx
    return len(preferred)


def _token_set(text: str) -> set[str]:
    stop = {"idea", "card", "link", "and", "the", "for", "with", "that", "this", "from"}
    return {tok for tok in TOKEN_RE.findall(text.lower()) if len(tok) >= 4 and tok not in stop}


def _normalize_filename(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _extract_mentioned_filename(source_text: str) -> str:
    match = FILE_IN_TEXT_RE.search(source_text or "")
    return match.group(1).strip() if match else ""


def _best_attachment_by_filename(
    attachments: list[dict[str, str]],
    source_text: str,
) -> tuple[str, str]:
    mentioned = _extract_mentioned_filename(source_text)
    if not mentioned:
        return "", ""
    target = _normalize_filename(mentioned)
    for att in attachments:
        filename = str(att.get("filename") or "")
        url = str(att.get("url") or "")
        if not filename or _is_bad_neighbor_link(filename, url):
            continue
        if _normalize_filename(filename) == target:
            return url, filename
    # fallback: substring containment if Jira trims or decorates filenames
    for att in attachments:
        filename = str(att.get("filename") or "")
        url = str(att.get("url") or "")
        if not filename or _is_bad_neighbor_link(filename, url):
            continue
        fn = _normalize_filename(filename)
        if target in fn or fn in target:
            return url, filename
    return "", ""


def _select_attachment_link(
    attachments: list[dict[str, str]],
    *,
    source_text: str,
    description: str,
) -> tuple[str, str]:
    if not attachments:
        return "", ""

    description_tokens = _token_set(description)
    source_tokens = _token_set(source_text)
    best: tuple[int, int, str, str] | None = None
    for att in attachments:
        filename = str(att.get("filename") or "")
        url = str(att.get("url") or "")
        if not filename or _is_bad_neighbor_link(filename, url):
            continue
        score = 0
        lowered_name = filename.lower()
        if _has_idea_card_label(lowered_name) or "proposal" in lowered_name or "deck" in lowered_name:
            score += 3
        overlap = len(_token_set(filename) & (source_tokens or description_tokens))
        if overlap >= 2:
            score += 2
        if _filename_extension_rank(filename) <= 3:
            score += 1
        if score <= 0:
            continue
        rank = _filename_extension_rank(filename)
        if best is None or (score, -rank) > (best[0], -best[1]):
            best = (score, rank, url, filename)

    if best:
        return best[2], best[3]

    # Fallback: if all we have is "see attachments", pick first non-estimate-style doc.
    for att in sorted(attachments, key=lambda a: _filename_extension_rank(str(a.get("filename") or ""))):
        filename = str(att.get("filename") or "")
        url = str(att.get("url") or "")
        if filename and not _is_bad_neighbor_link(filename, url):
            return url, filename
    return "", ""


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


def _normalize_verdict(verdict: dict[str, Any], description: str, attachments: list[dict[str, str]]) -> dict[str, Any]:
    verdict = dict(verdict)

    link = str(verdict.get("link") or "").strip()
    source_text = str(verdict.get("source_text") or "")
    attachment_statement = _idea_card_attachment_statement(description)

    normalized_link = ""
    normalized_reason = ""
    selection_strategy = "none"
    if link and _valid_idea_card_link(description, link, source_text):
        normalized_link = link
        selection_strategy = "llm_direct"
    else:
        # LLM sometimes returns a transformed SharePoint URL; remap to a URL we actually extracted.
        candidate_urls = _candidate_idea_card_urls(description, source_text)
        if candidate_urls:
            normalized_link = candidate_urls[0]
            normalized_reason = "Mapped labeled idea-card text to extracted description URL."
            selection_strategy = "description_url_map"

    # If description points to an attached file name, prefer the concrete Jira attachment link.
    by_name_url, by_name_file = _best_attachment_by_filename(attachments, source_text)
    attachment_hint = "attached" in source_text.lower() or "attachment" in source_text.lower()
    if by_name_url and (attachment_hint or _looks_like_portal_or_proxy(normalized_link)):
        normalized_link = by_name_url
        normalized_reason = f"Matched attachment filename '{by_name_file}' from source text and used Jira attachment URL."
        selection_strategy = "attachment_filename_match"

    # Never keep portal/proxy links when we have a strong attachment candidate.
    if normalized_link and _looks_like_portal_or_proxy(normalized_link):
        best_attachment_url, best_attachment_name = _select_attachment_link(
            attachments,
            source_text=source_text,
            description=description,
        )
        if best_attachment_url:
            normalized_link = best_attachment_url
            normalized_reason = f"Replaced portal/proxy URL with attachment '{best_attachment_name}'."
            selection_strategy = "attachment_over_proxy"

    if attachment_statement:
        verdict["has_idea_card"] = True
        verdict["confidence"] = max(float(verdict.get("confidence") or 0), 0.9)
        if normalized_link:
            verdict["link"] = normalized_link
            verdict["source_location"] = "attachment_section" if selection_strategy.startswith("attachment_") else "description_link"
            verdict["source_text"] = source_text or attachment_statement
            verdict["reason"] = (
                "Description references attachments and also includes an explicit idea-card link."
                if not normalized_reason
                else normalized_reason
            )
            verdict["link_selection_strategy"] = selection_strategy
        else:
            attachment_link, attachment_filename = _select_attachment_link(
                attachments,
                source_text=source_text or attachment_statement,
                description=description,
            )
            verdict["link"] = attachment_link
            verdict["source_location"] = "attachment_section" if attachment_link else "attachment_section"
            verdict["source_text"] = attachment_statement
            verdict["reason"] = (
                f"Description says idea card is in attachments; selected attachment '{attachment_filename}'."
                if attachment_link
                else "Description says the idea card is in the attachment section; no direct description link."
            )
            verdict["link_selection_strategy"] = "attachment_section_pick" if attachment_link else "attachment_section_none"
        return verdict

    if normalized_link:
        verdict["link"] = normalized_link
        verdict["source_location"] = "attachment_section" if selection_strategy.startswith("attachment_") else "description_link"
        if normalized_reason:
            verdict["reason"] = normalized_reason
        verdict["link_selection_strategy"] = selection_strategy
    else:
        if str(verdict.get("source_location") or "") == "attachment_section":
            attachment_link, attachment_filename = _select_attachment_link(
                attachments,
                source_text=source_text,
                description=description,
            )
            if attachment_link:
                verdict["link"] = attachment_link
                verdict["source_location"] = "attachment_section"
                verdict["reason"] = f"Selected attachment '{attachment_filename}' for attachment-section idea card mention."
                verdict["link_selection_strategy"] = "attachment_section_pick"
                return verdict
        verdict["link"] = ""
        verdict["source_location"] = verdict.get("source_location") or (
            "description_text" if verdict.get("has_idea_card") else "none"
        )
        if verdict.get("has_idea_card"):
            verdict["reason"] = verdict.get("reason") or "Idea card is mentioned, but no explicit idea-card URL was validated."
        verdict["link_selection_strategy"] = "none"
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


def _ask_llm(
    llm_client: Any,
    ticket_id: str,
    summary: str,
    description: str,
    urls: list[str],
    attachments: list[dict[str, str]],
) -> dict[str, Any]:
    attachment_view = [{"filename": a.get("filename", ""), "url": a.get("url", "")} for a in attachments]
    prompt = f"""Ticket: {ticket_id}
Summary: {summary}

URLs found in description:
{json.dumps(urls, indent=2)}

Attachments on ticket:
{json.dumps(attachment_view, indent=2)}

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


async def _fetch_description(ticket_client: Any, ticket_id: str) -> tuple[str, str, list[dict[str, str]]]:
    issue = await ticket_client.client.get_issue_by_key(ticket_id, fields=["summary", "description", "attachment"])
    fields = issue.get("fields") or {}
    return str(fields.get("summary") or ""), _description_to_text(fields.get("description")), _extract_attachments(fields)


async def _audit_one_ticket(
    ticket_client: Any,
    llm_client: Any,
    sem: asyncio.Semaphore,
    ticket_id: str,
) -> tuple[str, dict[str, Any] | None, str | None]:
    async with sem:
        try:
            summary, description, attachments = await _fetch_description(ticket_client, ticket_id)
            found_urls = _urls(description)
            raw_verdict = (
                _heuristic(description, found_urls)
                if llm_client is None
                else await asyncio.to_thread(_ask_llm, llm_client, ticket_id, summary, description, found_urls, attachments)
            )
            verdict = _normalize_verdict(raw_verdict, description, attachments)
            attachment_urls = [str(a.get("url") or "") for a in attachments if str(a.get("url") or "")]
            attachment_names = [str(a.get("filename") or "") for a in attachments if str(a.get("filename") or "")]
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
                "has_clear_file_reference": _has_clear_file_reference(verdict),
                "clear_file_location": _clear_file_location(verdict),
                "attachment_mentioned": bool(_idea_card_attachment_statement(description)),
                "attachment_count": len(attachments),
                "attachment_links": " | ".join(attachment_urls),
                "attachment_filenames": " | ".join(attachment_names),
                "link_selection_strategy": verdict.get("link_selection_strategy", "none"),
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
    clear_csv_path = out_dir / "jira_idea_card_clear_file_only.csv"
    clear_json_path = out_dir / "jira_idea_card_clear_file_only.json"

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
            "with_clear_file_reference": sum(1 for row in rows if row["has_clear_file_reference"]),
            "description_only_mentions": sum(
                1
                for row in rows
                if row["has_idea_card"] and not row["has_clear_file_reference"]
            ),
        },
        "tickets": rows,
        "errors": errors,
    }
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    clear_rows = [row for row in rows if row["has_clear_file_reference"]]
    if clear_rows:
        with clear_csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(clear_rows[0].keys()))
            writer.writeheader()
            writer.writerows(clear_rows)
    else:
        clear_csv_path.write_text("", encoding="utf-8")

    clear_output = {
        "summary": {
            "tickets_with_clear_file_reference": len(clear_rows),
            "total_tickets_processed": len(rows),
            "errors": len(errors),
        },
        "tickets": clear_rows,
        "errors": errors,
    }
    clear_json_path.write_text(json.dumps(clear_output, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Tickets checked: {len(rows)}")
    print(f"With idea card: {output['summary']['with_idea_card']}")
    print(f"With SharePoint idea-card link: {output['summary']['with_sharepoint_idea_card_link']}")
    print(f"With clear file reference: {output['summary']['with_clear_file_reference']}")
    print(f"Description-only mentions: {output['summary']['description_only_mentions']}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Clear-file CSV: {clear_csv_path}")
    print(f"Clear-file JSON: {clear_json_path}")


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
