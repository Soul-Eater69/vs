from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vs_app.modules.rag.extraction.backends import extract_uploaded_file_text


def _backend_summary(result: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(result.get("metadata") or {})
    return {
        "backend": result.get("backend"),
        "filename": result.get("filename"),
        "extension": metadata.get("extension"),
        "chars": metadata.get("chars", 0),
        "words": metadata.get("words", 0),
        "element_count": metadata.get("element_count", 0),
        "tables_detected": metadata.get("tables_detected", 0),
        "warnings": list(metadata.get("warnings") or []),
    }


def _render_markdown(result: dict[str, Any]) -> str:
    metadata = dict(result.get("metadata") or {})
    body = str(result.get("markdown") or result.get("text") or "").strip()
    lines = [
        f"# Extraction Backend: {result.get('backend') or 'unknown'}",
        "",
        "## Summary",
        f"- Filename: {result.get('filename') or ''}",
        f"- Extension: {metadata.get('extension') or ''}",
        f"- Chars: {metadata.get('chars', 0)}",
        f"- Words: {metadata.get('words', 0)}",
        f"- Element count: {metadata.get('element_count', 0)}",
        f"- Tables detected: {metadata.get('tables_detected', 0)}",
    ]
    warnings = list(metadata.get("warnings") or [])
    if warnings:
        lines.append(f"- Warnings: {'; '.join(str(w) for w in warnings)}")
    lines.extend(["", "## Extracted Markdown", "", body])
    return "\n".join(lines).rstrip() + "\n"


def _unstructured_available() -> bool:
    try:
        from unstructured.partition.auto import partition as _partition  # noqa: F401
    except ImportError:
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare current and Unstructured idea-card extraction backends."
    )
    parser.add_argument("file_path", help="Path to a PPT/PPTX/PDF/DOC/DOCX idea-card file.")
    parser.add_argument(
        "--out",
        default="output/extraction_compare",
        help="Directory for markdown and comparison JSON output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.file_path).expanduser().resolve()
    if not input_path.is_file():
        print(f"File not found: {input_path}", file=sys.stderr)
        return 2

    file_bytes = input_path.read_bytes()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    comparison: dict[str, Any] = {
        "filename": input_path.name,
        "source_path": str(input_path),
        "backends": {},
    }

    current = extract_uploaded_file_text(file_bytes, input_path.name, backend="current")
    (out_dir / f"{input_path.stem}.current.md").write_text(
        _render_markdown(current),
        encoding="utf-8",
    )
    comparison["backends"]["current"] = _backend_summary(current)

    if _unstructured_available():
        unstructured = extract_uploaded_file_text(
            file_bytes,
            input_path.name,
            backend="unstructured",
        )
        (out_dir / f"{input_path.stem}.unstructured.md").write_text(
            _render_markdown(unstructured),
            encoding="utf-8",
        )
        comparison["backends"]["unstructured"] = _backend_summary(unstructured)
    else:
        comparison["backends"]["unstructured"] = {
            "available": False,
            "warnings": ["unstructured is not installed"],
        }

    comparison_path = out_dir / f"{input_path.stem}.comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote comparison to {comparison_path}")
    print(f"Wrote current markdown to {out_dir / f'{input_path.stem}.current.md'}")
    if comparison["backends"].get("unstructured", {}).get("available") is False:
        print("Skipped Unstructured markdown because unstructured is not installed.")
    else:
        print(f"Wrote Unstructured markdown to {out_dir / f'{input_path.stem}.unstructured.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
