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
        "--dry-run",
        action="store_true",
        help="Report counts only; write no file and make no Azure/LLM calls.",
    )
    parser.add_argument(
        "--classify-support",
        action="store_true",
        help="(reserved) Enable LLM support classification. Off by default; not used in this PR.",
    )
    parser.add_argument(
        "--summary-enrich",
        action="store_true",
        help="(reserved) Enable LLM summary enrichment. Off by default; not used in this PR.",
    )
    return parser


def load_payload(path: str) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    "Reserved flag accepted but not implemented in this PR; "
    "no LLM enrichment will run."
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
    docs = theme_generation_documents_from_gt_payload(payload, limit=args.limit)
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
    print(f"  classify_support: {args.classify_support} (not used in this PR)")
    print(f"  summary_enrich  : {args.summary_enrich} (not used in this PR)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
