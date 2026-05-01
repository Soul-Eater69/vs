"""Find summaries that will not be indexed into historical FAISS.

Usage:
  py -3 jobs/audit_faiss_index.py
  py -3 jobs/audit_faiss_index.py --input-dir ticket_data --out missed.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    summaries_path = input_dir / args.summary_file
    if not summaries_path.exists():
        raise SystemExit(f"Summary file not found: {summaries_path}")

    summaries = load_summaries(summaries_path)
    report = audit_summaries(summaries)

    out_path = Path(args.out)
    write_json(out_path, report)

    print(f"Source summaries: {report['source_summary_count']}")
    print(f"Indexable:        {report['indexable_summary_count']}")
    print(f"Skipped:          {report['skipped_summary_count']}")
    if report["skipped_summary_docs"]:
        print("")
        print("Skipped ticket IDs:")
        for row in report["skipped_summary_docs"]:
            print(f"  {row.get('ticket_id') or '<missing ticket_id>'}: {row['reason']}")
    print("")
    print(f"Wrote: {out_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit summaries.json for records FAISS will skip.",
    )
    parser.add_argument("--input-dir", default="ticket_data")
    parser.add_argument("--summary-file", default="summaries.json")
    parser.add_argument("--out", default="missed.json")
    return parser.parse_args()


def load_summaries(path: Path) -> list[dict]:
    payload = read_json(path)
    if isinstance(payload, dict):
        rows = payload.get("summaries") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def audit_summaries(summaries: list[dict]) -> dict[str, Any]:
    from vs_app.modules.rag.query.retrieval_summary import format_structured_summary_text

    indexable: list[str] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    duplicates: list[str] = []

    for index, row in enumerate(summaries):
        ticket_id = str(row.get("ticket_id") or row.get("key") or "").strip()
        if ticket_id:
            key = ticket_id.upper()
            if key in seen:
                duplicates.append(ticket_id)
            seen.add(key)

        formatted = format_structured_summary_text(row).strip()
        if formatted:
            if ticket_id:
                indexable.append(ticket_id)
            continue

        skipped.append(
            {
                "ticket_id": ticket_id,
                "index": index,
                "reason": "empty_formatted_summary_text",
                "present_fields": sorted(
                    key for key, value in row.items() if value not in ("", [], {}, None)
                ),
            }
        )

    return {
        "source_summary_count": len(summaries),
        "indexable_summary_count": len(summaries) - len(skipped),
        "skipped_summary_count": len(skipped),
        "indexable_ticket_ids": indexable,
        "skipped_summary_docs": skipped,
        "duplicate_ticket_ids": sorted(set(duplicates)),
    }


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
