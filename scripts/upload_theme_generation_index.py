"""Dry-run-safe CLI for the Theme-generation POC index.

Reads JSONL documents (produced by ``build_theme_generation_documents``) and,
by default, reports what *would* happen with zero Azure calls. Real Azure
operations (index create/recreate, document upload) run only with ``--upload``.
Recreate additionally requires ``THEME_GEN_ALLOW_RECREATE=1`` and passes a name
guard. This script does not touch Jira and does not generate themes at runtime.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.ingestion.upload.azure_search_client import resolve_index_name
from vs_app.ingestion.upload.index_manager import create_theme_generation_index
from vs_app.ingestion.upload.uploader import (
    embed_idmt_documents,
    read_jsonl,
    summarize_documents,
    upload_theme_generation_documents,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", required=True, help="Path to JSONL documents.")
    parser.add_argument("--index-name", default=None, help="Override the POC index name.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="DEFAULT. Report only and make no Azure or embedding calls; "
        "index create/recreate is skipped even if requested.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Perform real Azure operations (index ops + document upload); "
        "disables the default dry-run.",
    )
    parser.add_argument("--create-index", action="store_true", help="Create the index (with --upload).")
    parser.add_argument(
        "--recreate-index",
        action="store_true",
        help="Delete+recreate the index (with --upload and THEME_GEN_ALLOW_RECREATE=1).",
    )
    parser.add_argument(
        "--embed-idmt",
        action="store_true",
        help="Generate content_vector for IDMT docs only via embed_batch.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    dry_run = not args.upload  # dry-run unless --upload is explicit

    docs = read_jsonl(args.jsonl)
    if args.embed_idmt:
        docs = embed_idmt_documents(docs)

    index_name = resolve_index_name(args.index_name)
    index_action = "none"
    if not dry_run and (args.create_index or args.recreate_index):
        result = create_theme_generation_index(
            index_name=index_name,
            recreate=args.recreate_index,
        )
        index_action = result["action"]

    upload_result = upload_theme_generation_documents(
        docs=docs, index_name=index_name, dry_run=dry_run
    )

    summary = summarize_documents(docs)
    print("Theme-generation POC index")
    print(f"  index name        : {index_name}")
    print(f"  mode              : {'dry-run' if dry_run else 'upload'}")
    print(f"  docs read         : {summary['total']}")
    print(f"  idmt docs         : {summary['idmt']}")
    print(f"  theme docs        : {summary['theme']}")
    print(f"  docs with vectors : {summary['with_vectors']}")
    print(f"  create requested  : {args.create_index}")
    print(f"  recreate requested: {args.recreate_index}")
    print(f"  index action      : {index_action}")
    print(f"  uploaded          : {upload_result['uploaded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
