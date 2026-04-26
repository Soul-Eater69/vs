"""
Test actual SharePoint access for useful links from link_access_report.json.

Uses SharePointClient.resolve_browser_url() — resolves any SharePoint browser
URL via Graph API /shares/{token}/driveItem without needing site/drive IDs.

Usage:
  uv run python scripts/sharepoint_access_test.py
  uv run python scripts/sharepoint_access_test.py --input ticket_data/link_access_report.json
  uv run python scripts/sharepoint_access_test.py --all-links
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from vs_app.integrations.clients.sharepoint import SharePointClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_SHAREPOINT_HOSTS = ("sharepoint.com", "sharepoint-df.com", "my.sharepoint")


def _is_sharepoint(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(sp in host for sp in _SHAREPOINT_HOSTS)


def run(input_file: Path, out_file: Path, all_links: bool) -> None:
    data = json.loads(input_file.read_text(encoding="utf-8"))
    tickets: dict = data.get("tickets") or {}

    links_to_test = [
        {"ticket_id": tid, **lnk}
        for tid, record in tickets.items()
        for lnk in (record.get("links") or [])
        if (all_links or lnk.get("is_useful")) and _is_sharepoint(lnk["url"])
    ]

    if not links_to_test:
        logger.warning("No SharePoint links to test in %s", input_file)
        return

    logger.info("Testing %d SharePoint links...", len(links_to_test))
    sp = SharePointClient()
    if not sp.access_token:
        logger.error("Could not obtain Graph token — check GRAPH_ACCESS_TOKEN")
        return

    results = []
    success = 0
    for item in links_to_test:
        url = item["url"]
        file_meta = sp.resolve_browser_url(url)
        if file_meta:
            success += 1
            outcome = {
                "result": "success",
                "name": file_meta.get("name"),
                "size": file_meta.get("size"),
                "mime_type": (file_meta.get("file") or {}).get("mimeType"),
                "web_url": file_meta.get("webUrl"),
            }
            logger.info("OK   [%s] %s → %s", item["ticket_id"], url[:70], outcome["name"])
        else:
            outcome = {"result": "failed"}
            logger.info("FAIL [%s] %s", item["ticket_id"], url[:70])

        results.append({**item, **outcome})

    by_result: dict[str, int] = {}
    for r in results:
        by_result[r["result"]] = by_result.get(r["result"], 0) + 1

    output = {
        "summary": {
            "total_tested": len(results),
            "success": success,
            "failed": len(results) - success,
            "by_result": by_result,
        },
        "links": results,
    }

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")

    print("-" * 50)
    print(f"Tested   : {len(results)}")
    print(f"Success  : {success}")
    print(f"Failed   : {len(results) - success}")
    print(f"Output   : {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="ticket_data/link_access_report.json")
    parser.add_argument("--out-file", default="ticket_data/sharepoint_access_test.json")
    parser.add_argument("--all-links", action="store_true", help="Include non-useful links too.")
    args = parser.parse_args()
    run(Path(args.input), Path(args.out_file), args.all_links)


if __name__ == "__main__":
    main()
