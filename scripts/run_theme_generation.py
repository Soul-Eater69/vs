"""Manual-only CLI to run the Theme-generation POC locally.

Default is **dry-run**: it validates inputs, loads the local stage catalog for
reporting, and prints a plan WITHOUT constructing any embedding/Azure/LLM client
and WITHOUT any network call. Real Azure read-search + embedding + LLM happen
only with the explicit ``--run`` flag.

This is a manual experiment harness only: no Jira, no upload, no index
create/delete, no API/UI wiring.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.ingestion.theme_generation.orchestrator import generate_themes_for_idea
from vs_app.ingestion.upload.azure_search_client import resolve_index_name
from vs_app.modules.stages.stage_catalog import get_allowed_stages, load_stage_catalog

DEFAULT_STAGE_CATALOG = "data/value_stream_stage_map.json"


# ---------------------------------------------------------------------------
# Core (injected dependencies; no client construction here)
# ---------------------------------------------------------------------------


def run_theme_generation(
    *,
    idea_context: str,
    value_streams: list[str],
    stage_catalog: dict[str, Any],
    embedder: Any,
    search_adapter: Any,
    llm: Any,
    top_k_idmt: int,
    max_examples: int,
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Embed the idea once, then orchestrate one Theme per selected value stream."""
    query_vector = embedder.embed(idea_context)
    return generate_themes_for_idea(
        idea_context=idea_context,
        selected_value_streams=value_streams,
        stage_catalog=stage_catalog,
        llm=llm,
        search_client=search_adapter,
        query_vector=query_vector,
        top_k_idmt=top_k_idmt,
        max_examples=max_examples,
        index_name=index_name,
    )


# ---------------------------------------------------------------------------
# Lazy factories (only called on the --run path)
# ---------------------------------------------------------------------------


def make_embedding_client() -> Any:
    from vs_app.integrations.clients.embedding import EmbeddingClient

    return EmbeddingClient()


def make_generation_service() -> Any:
    from vs_app.integrations.clients.generation_service import GenerationService

    return GenerationService()


def make_theme_generation_search_adapter(index_name: str) -> Any:
    from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient
    from vs_app.ingestion.theme_generation.search_adapter import (
        ThemeGenerationSearchAdapter,
    )

    client = AzureDirectSearchClient(index_name=index_name)
    return ThemeGenerationSearchAdapter(client=client, index_name=index_name)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idea", default=None, help="Idea / IDMT context text.")
    parser.add_argument("--idea-file", default=None, help="Path to a file with the idea text.")
    parser.add_argument(
        "--value-streams",
        required=True,
        help="Comma-separated selected value stream names.",
    )
    parser.add_argument(
        "--stage-catalog",
        default=DEFAULT_STAGE_CATALOG,
        help=f"Stage catalog JSON path (default: {DEFAULT_STAGE_CATALOG}).",
    )
    parser.add_argument("--index-name", default=None, help="Override the POC index name.")
    parser.add_argument("--top-k-idmt", type=int, default=15)
    parser.add_argument("--max-examples", type=int, default=5)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="DEFAULT. Plan only; construct no clients and make no Azure/embedding/LLM calls.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Live mode: real Azure read-search + embedding + LLM. Disables dry-run.",
    )
    parser.add_argument("--output", default=None, help="Optional path to write the JSON result.")
    return parser


def resolve_idea(args: argparse.Namespace) -> str:
    if args.idea is not None and args.idea_file is not None:
        raise SystemExit("ERROR: pass only one of --idea or --idea-file, not both.")
    if args.idea is not None:
        idea = args.idea
    elif args.idea_file is not None:
        path = Path(args.idea_file)
        if not path.exists():
            raise SystemExit(f"ERROR: --idea-file not found: {path}")
        idea = path.read_text(encoding="utf-8")
    else:
        raise SystemExit("ERROR: provide --idea or --idea-file.")
    if not idea.strip():
        raise SystemExit("ERROR: idea text is empty.")
    return idea


def parse_value_streams(raw: str) -> list[str]:
    value_streams = [" ".join(part.split()) for part in (raw or "").split(",")]
    value_streams = [vs for vs in value_streams if vs]
    if not value_streams:
        raise SystemExit("ERROR: --value-streams must contain at least one name.")
    return value_streams


def load_catalog_safely(path: str, warnings: list[str]) -> dict[str, Any]:
    """Load the stage catalog JSON; on failure return {} + a warning (dry-run safe)."""
    try:
        return load_stage_catalog(path=path, source="json")
    except Exception as exc:  # noqa: BLE001 - missing/invalid catalog must not crash dry-run
        warnings.append(f"stage catalog could not be loaded from {path}: {type(exc).__name__}: {exc}")
        return {}


def build_dry_run_plan(
    *,
    idea: str,
    value_streams: list[str],
    catalog: dict[str, Any],
    index_name: str,
    stage_catalog_path: str,
    top_k_idmt: int,
    max_examples: int,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "resolved_index_name": index_name,
        "idea_length": len(idea),
        "selected_value_streams": value_streams,
        "stage_catalog_path": stage_catalog_path,
        "allowed_stage_counts": {
            vs: len(get_allowed_stages(vs, catalog)) for vs in value_streams
        },
        "top_k_idmt": top_k_idmt,
        "max_examples": max_examples,
        "would_run": ["embedding", "azure_read_search", "llm"],
        "skipped": True,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    dry_run = not args.run

    idea = resolve_idea(args)
    value_streams = parse_value_streams(args.value_streams)
    index_name = resolve_index_name(args.index_name)
    warnings: list[str] = []
    catalog = load_catalog_safely(args.stage_catalog, warnings)

    if dry_run:
        plan = build_dry_run_plan(
            idea=idea,
            value_streams=value_streams,
            catalog=catalog,
            index_name=index_name,
            stage_catalog_path=args.stage_catalog,
            top_k_idmt=args.top_k_idmt,
            max_examples=args.max_examples,
            warnings=warnings,
        )
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    # --- live mode (--run): construct real clients lazily, here only ---
    embedder = make_embedding_client()
    adapter = make_theme_generation_search_adapter(index_name)
    llm = make_generation_service()

    results = run_theme_generation(
        idea_context=idea,
        value_streams=value_streams,
        stage_catalog=catalog,
        embedder=embedder,
        search_adapter=adapter,
        llm=llm,
        top_k_idmt=args.top_k_idmt,
        max_examples=args.max_examples,
        index_name=index_name,
    )

    payload = json.dumps(results, indent=2, ensure_ascii=False)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
