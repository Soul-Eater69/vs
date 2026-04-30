"""Download one idea-card file per ticket from audit output.

Input formats supported:
1) {"tickets": [ ...rows... ]}
2) {"tickets": {"IDMT-1": {...record...}, ...}}
3) [ ...rows... ]

Each row should contain:
- ticket_id
- idea_card_link

Behavior:
- For each ticket, tries only `idea_card_link`.
- If primary link is missing, mark failed and move on.
- If primary link download fails, mark failed and move on.
- Saves first successful download as <ticket_id><ext> under output dir.
- Writes JSON + CSV report including failure reasons.

Usage:
  py -3 scripts/download_idea_card_files.py --input output/idea_card_audit/jira_idea_card_description_audit.json
"""

from __future__ import annotations

import asyncio
import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

from vs_app.container import build_ticket_fetcher
from vs_app.integrations.clients.sharepoint import SharePointClient

load_dotenv()

_FILE_EXT_RE = re.compile(r"\.(pptx|ppt|pdf|docx|doc|pptm|xlsx|xlsm|xls)$", re.IGNORECASE)


def _parse_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        tickets = data.get("tickets")
        if isinstance(tickets, list):
            return [row for row in tickets if isinstance(row, dict)]
        if isinstance(tickets, dict):
            out: list[dict[str, Any]] = []
            for tid, record in tickets.items():
                if isinstance(record, dict):
                    merged = {"ticket_id": tid}
                    merged.update(record)
                    out.append(merged)
            return out
    return []


def _ext_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = path.rsplit("/", 1)[-1]
    m = _FILE_EXT_RE.search(name)
    return f".{m.group(1).lower()}" if m else ".bin"


async def _download_with_jira_client(ticket_client: Any, url: str) -> tuple[bool, bytes | None, str]:
    try:
        content = await ticket_client.download_attachment(url)
    except Exception as exc:
        return False, None, f"download_exception:{type(exc).__name__}:{exc}"
    if not content:
        return False, None, "empty_content"
    if not isinstance(content, (bytes, bytearray)):
        return False, None, f"unexpected_content_type:{type(content).__name__}"
    return True, bytes(content), "ok"


def _candidate_links(row: dict[str, Any]) -> list[str]:
    primary = str(row.get("idea_card_link") or "").strip()
    if primary:
        return [primary]
    return []


async def run(input_path: Path, out_dir: Path, *, verbose: bool = True) -> tuple[Path, Path]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = _parse_rows(data)
    if not rows:
        raise RuntimeError("No ticket rows found in input JSON.")

    out_dir.mkdir(parents=True, exist_ok=True)
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    sharepoint_client = SharePointClient()

    report_rows: list[dict[str, Any]] = []
    total = len(rows)
    async with build_ticket_fetcher(source="jira", verify_ssl=False, sharepoint_client=sharepoint_client) as ticket_client:
        for idx, row in enumerate(rows, start=1):
            ticket_id = str(row.get("ticket_id") or "").strip().upper()
            if not ticket_id:
                continue

            links = _candidate_links(row)
            if verbose:
                print(f"[{idx}/{total}] {ticket_id}: {len(links)} candidate link(s)")
            if not links:
                report_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "status": "failed",
                        "saved_path": "",
                        "selected_link": "",
                        "reason": "no_candidate_links",
                        "attempted_links": "",
                    }
                )
                continue

            success = False
            failure_reasons: list[str] = []
            saved_path = ""
            selected_link = ""
            for link in links:
                ext = _ext_from_url(link)
                target = files_dir / f"{ticket_id}{ext}"
                if target.exists() and target.stat().st_size > 0:
                    success = True
                    selected_link = link
                    saved_path = str(target)
                    report_rows.append(
                        {
                            "ticket_id": ticket_id,
                            "status": "already_downloaded",
                            "saved_path": saved_path,
                            "selected_link": selected_link,
                            "reason": "file_exists",
                            "attempted_links": " | ".join(links),
                        }
                    )
                    if verbose:
                        print(f"  -> already_downloaded: {target.name}")
                    break

                if verbose:
                    print(f"  -> trying: {link[:120]}")
                ok, content, reason = await _download_with_jira_client(ticket_client, link)

                if not ok or content is None:
                    failure_reasons.append(f"{link}=>{reason}")
                    if verbose:
                        print(f"     failed: {reason}")
                    continue

                target.write_bytes(content)
                success = True
                selected_link = link
                saved_path = str(target)
                if verbose:
                    print(f"     downloaded: {target.name} ({len(content)} bytes)")
                report_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "status": "downloaded",
                        "saved_path": saved_path,
                        "selected_link": selected_link,
                        "reason": "ok",
                        "attempted_links": " | ".join(links),
                    }
                )
                break

            if not success:
                report_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "status": "failed",
                        "saved_path": "",
                        "selected_link": "",
                        "reason": "; ".join(failure_reasons) if failure_reasons else "unknown_failure",
                        "attempted_links": " | ".join(links),
                    }
                )
                if verbose:
                    print(f"  -> failed ticket: {'; '.join(failure_reasons) if failure_reasons else 'unknown_failure'}")

    json_out = out_dir / "download_report.json"
    csv_out = out_dir / "download_report.csv"
    summary = {
        "total_tickets": len({r["ticket_id"] for r in report_rows}),
        "downloaded": sum(1 for r in report_rows if r["status"] == "downloaded"),
        "already_downloaded": sum(1 for r in report_rows if r["status"] == "already_downloaded"),
        "failed": sum(1 for r in report_rows if r["status"] == "failed"),
    }
    payload = {"summary": summary, "tickets": report_rows}
    json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    with csv_out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["ticket_id", "status", "saved_path", "selected_link", "reason", "attempted_links"],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    return json_out, csv_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Download idea-card files by ticket from audit JSON.")
    parser.add_argument("--input", required=True, help="Path to audit JSON containing ticket links.")
    parser.add_argument("--out-dir", default="ticket_data/idea_card_downloads")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-ticket progress logs.")
    args = parser.parse_args()

    json_out, csv_out = asyncio.run(run(Path(args.input), Path(args.out_dir), verbose=not args.quiet))
    print(f"JSON report: {json_out}")
    print(f"CSV report: {csv_out}")


if __name__ == "__main__":
    main()
