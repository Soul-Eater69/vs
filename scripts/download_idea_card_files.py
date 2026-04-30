"""Download one idea-card file per ticket from audit output.

Input formats supported:
1) {"tickets": [ ...rows... ]}
2) {"tickets": {"IDMT-1": {...record...}, ...}}
3) [ ...rows... ]

Each row may contain:
- ticket_id
- idea_card_link
- attachment_links (pipe-separated string)

Behavior:
- For each ticket, tries candidate links in priority order:
  1) idea_card_link
  2) attachment_links entries
- Saves first successful download as <ticket_id><ext> under output dir.
- Writes JSON + CSV report including failure reasons.

Usage:
  py -3 scripts/download_idea_card_files.py --input output/idea_card_audit/jira_idea_card_description_audit.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests
from dotenv import load_dotenv

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


def _split_links(raw: str) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split("|") if part.strip()]


def _ext_from_url(url: str) -> str:
    path = unquote(urlparse(url).path or "")
    name = path.rsplit("/", 1)[-1]
    m = _FILE_EXT_RE.search(name)
    return f".{m.group(1).lower()}" if m else ".bin"


def _is_sharepoint(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "sharepoint.com" in host or "sharepoint-df.com" in host or "my.sharepoint" in host


def _download_sharepoint(url: str, sp: SharePointClient) -> tuple[bool, bytes | None, str]:
    if not sp.access_token:
        return False, None, "missing_sharepoint_graph_token"
    item = sp.resolve_browser_url(url)
    if not item:
        return False, None, "graph_resolve_failed"
    download_url = item.get("@microsoft.graph.downloadUrl")
    if not download_url:
        return False, None, "missing_graph_download_url"
    content = sp.get_file_content_by_url(download_url)
    if content is None:
        return False, None, "graph_download_failed"
    return True, content, "ok"


def _download_generic(url: str, jira_token: str) -> tuple[bool, bytes | None, str]:
    headers = {}
    if jira_token:
        headers["Authorization"] = f"Bearer {jira_token}"
    try:
        resp = requests.get(url, headers=headers, timeout=45, allow_redirects=True)
    except Exception as exc:
        return False, None, f"request_exception:{type(exc).__name__}"
    if resp.status_code != 200:
        return False, None, f"http_{resp.status_code}"
    return True, resp.content, "ok"


def _candidate_links(row: dict[str, Any]) -> list[str]:
    links: list[str] = []
    primary = str(row.get("idea_card_link") or "").strip()
    if primary:
        links.append(primary)
    links.extend(_split_links(str(row.get("attachment_links") or "")))
    deduped: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link not in seen:
            seen.add(link)
            deduped.append(link)
    return deduped


def run(input_path: Path, out_dir: Path) -> tuple[Path, Path]:
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    data = json.loads(input_path.read_text(encoding="utf-8"))
    rows = _parse_rows(data)
    if not rows:
        raise RuntimeError("No ticket rows found in input JSON.")

    out_dir.mkdir(parents=True, exist_ok=True)
    files_dir = out_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    jira_token = os.environ.get("JIRA_TOKEN", "")
    sp = SharePointClient()

    report_rows: list[dict[str, Any]] = []
    for row in rows:
        ticket_id = str(row.get("ticket_id") or "").strip().upper()
        if not ticket_id:
            continue

        links = _candidate_links(row)
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
                break

            if _is_sharepoint(link):
                ok, content, reason = _download_sharepoint(link, sp)
            else:
                ok, content, reason = _download_generic(link, jira_token)

            if not ok or content is None:
                failure_reasons.append(f"{link}=>{reason}")
                continue

            target.write_bytes(content)
            success = True
            selected_link = link
            saved_path = str(target)
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
    args = parser.parse_args()

    json_out, csv_out = run(Path(args.input), Path(args.out_dir))
    print(f"JSON report: {json_out}")
    print(f"CSV report: {csv_out}")


if __name__ == "__main__":
    main()

