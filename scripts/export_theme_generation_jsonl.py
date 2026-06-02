"""Offline export of Theme-generation POC documents to JSONL.

INPUT IS AN EXISTING GROUND-TRUTH JSON FILE, NOT JIRA. This script reads an
offline stage-ground-truth payload already produced by
``scripts/build_stage_ground_truth.py`` (it does NOT fetch Jira itself) and
writes Theme-generation documents (one IDMT doc + one theme doc per GROUP) as
JSONL, ready for the Feature 10 uploader. This is an offline backfill/export
step:

- Writes JSONL only. Never connects to Azure, never creates an index, never
  uploads.
- LLM support classification / summary enrichment are OFF by default (no LLM
  calls unless explicitly enabled; flags are placeholders for a follow-up).
- ``--dry-run`` reports counts and writes no file.

This script does not generate themes at runtime and is not part of any runtime
prediction path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.ingestion.index_documents.theme_generation_export import (
    index_summary_by_ticket,
    index_support_by_ticket,
    theme_generation_documents_from_gt_payload,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gt-input",
        required=True,
        help=(
            "Path to an existing stage ground-truth JSON file produced by "
            "scripts/build_stage_ground_truth.py. This script reads that file; "
            "it does NOT fetch Jira."
        ),
    )
    parser.add_argument("--out", default=None, help="Output JSONL path (required unless --dry-run).")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of tickets.")
    parser.add_argument(
        "--support-input",
        default=None,
        help=(
            "Optional precomputed support JSON/JSONL keyed by ticket_id, with "
            "value_stream_support and/or stage_support (e.g. a Feature 8B dataset). "
            "Missing tickets keep blank support fields."
        ),
    )
    parser.add_argument(
        "--summary-input",
        default=None,
        help=(
            "Optional precomputed summary JSON/JSONL keyed by ticket_id, with "
            "key_terms / stakeholders / systems_and_products. Missing tickets keep []."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts only; write no file and make no Azure/LLM calls.",
    )
    parser.add_argument(
        "--classify-support",
        action="store_true",
        help="(reserved, no-op) Direct LLM support classification is not implemented; "
        "use --support-input for precomputed enrichment.",
    )
    parser.add_argument(
        "--summary-enrich",
        action="store_true",
        help="(reserved, no-op) Direct LLM summary enrichment is not implemented; "
        "use --summary-input for precomputed enrichment.",
    )
    return parser


def load_payload(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl_or_json(path: str) -> object:
    """Load a JSON document, or a JSONL file (one JSON value per line)."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return rows


def write_jsonl(path: str, docs: list[dict]) -> None:
    lines = "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs)
    Path(path).write_text(lines, encoding="utf-8")


def summarize(docs: list[dict]) -> dict[str, int]:
    return {
        "total": len(docs),
        "idmt": sum(1 for doc in docs if doc.get("document_type") == "idmt"),
        "theme": sum(1 for doc in docs if doc.get("document_type") == "theme"),
    }


RESERVED_FLAG_WARNING = (
    "Direct LLM enrichment is not implemented. "
    "Use --support-input and --summary-input for precomputed enrichment."
)


def warn_reserved_flags(args: argparse.Namespace) -> bool:
    """Warn (no-op) if a reserved enrichment flag was passed. Returns True if so."""
    if args.classify_support or args.summary_enrich:
        print(f"WARNING: {RESERVED_FLAG_WARNING}", flush=True)
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    warn_reserved_flags(args)

    payload = load_payload(args.gt_input)

    # Optional precomputed enrichment; invalid path fails clearly (the loader
    # raises FileNotFoundError). A missing ticket simply keeps blank fields.
    support_by_ticket = (
        index_support_by_ticket(load_jsonl_or_json(args.support_input))
        if args.support_input
        else None
    )
    summary_by_ticket = (
        index_summary_by_ticket(load_jsonl_or_json(args.summary_input))
        if args.summary_input
        else None
    )

    docs = theme_generation_documents_from_gt_payload(
        payload,
        limit=args.limit,
        support_by_ticket=support_by_ticket,
        summary_by_ticket=summary_by_ticket,
    )
    counts = summarize(docs)

    wrote = False
    if not args.dry_run:
        if not args.out:
            print("ERROR: --out is required unless --dry-run", flush=True)
            return 2
        write_jsonl(args.out, docs)
        wrote = True

    print("Theme-generation JSONL export")
    print(f"  gt input     : {args.gt_input}")
    print(f"  mode         : {'dry-run' if args.dry_run else 'write'}")
    print(f"  docs total   : {counts['total']}")
    print(f"  idmt docs    : {counts['idmt']}")
    print(f"  theme docs   : {counts['theme']}")
    print(f"  output       : {args.out if wrote else '(none)'}")
    print(f"  support input: {args.support_input or '(none)'} "
          f"({len(support_by_ticket or {})} tickets)")
    print(f"  summary input: {args.summary_input or '(none)'} "
          f"({len(summary_by_ticket or {})} tickets)")
    print(f"  classify_support: {args.classify_support} (reserved no-op)")
    print(f"  summary_enrich  : {args.summary_enrich} (reserved no-op)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
