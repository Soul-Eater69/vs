"""Debug local document attachment extraction.

This script intentionally wraps the repo's existing extraction functions instead
of introducing a separate parsing path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vs_app.shared.text_cleaning import clean_extracted_text


SUPPORTED_EXTENSIONS = {".pptx", ".ppt", ".pdf", ".docx", ".doc"}
TEXT_SUFFIX = {
    "markdown": ".md",
    "text": ".txt",
    "json": ".json",
}


def main() -> int:
    args = parse_args()
    path = Path(args.file_path).expanduser()
    if not path.exists() or not path.is_file():
        raise SystemExit(f"File not found: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise SystemExit(f"Unsupported file extension '{ext or '<none>'}'. Supported: {supported}")

    file_bytes = path.read_bytes()
    extraction = extract_existing(file_bytes, ext=ext, args=args)
    debug_payload = build_debug_payload(
        path=path,
        ext=ext,
        extraction=extraction,
        ocr_enabled=bool(args.ocr),
        max_slides=args.max_slides,
        max_pages=args.max_pages,
    )
    rendered = render_payload(debug_payload, output_format=args.format)

    if args.out:
        out_path = write_output(rendered, input_path=path, out_dir=Path(args.out), output_format=args.format)
        print(f"Wrote extraction debug: {out_path}")
    else:
        print(rendered)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Show raw text/chunk output from the repo's existing file extractors.",
    )
    parser.add_argument("file_path", help="Local PPT/PPTX/PDF/DOC/DOCX file path.")
    parser.add_argument(
        "--format",
        choices=("markdown", "text", "json"),
        default="markdown",
        help="Output format. Default: markdown.",
    )
    parser.add_argument(
        "--out",
        help="Optional output directory. If omitted, output is printed to stdout.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Pass ocr_enabled=True for PDF extraction.",
    )
    parser.add_argument(
        "--max-slides",
        type=int,
        default=60,
        help="Maximum PPT/PPTX slides passed to extract_pptx. Default: 60.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="Maximum PDF pages passed to extract_pdf. Default: 50.",
    )
    return parser.parse_args()


def extract_existing(file_bytes: bytes, *, ext: str, args: argparse.Namespace) -> dict[str, Any]:
    if ext in {".pptx", ".ppt"}:
        from vs_app.integrations.files.pptx_extractor import extract_pptx

        result = extract_pptx(file_bytes, max_slides=args.max_slides)
        return {"extraction_type": "pptx", "result": result}

    if ext == ".pdf":
        from vs_app.integrations.files.pdf_extractor import extract_pdf

        result = extract_pdf(file_bytes, ocr_enabled=bool(args.ocr), max_pages=args.max_pages)
        return {"extraction_type": "pdf", "result": result}

    if ext in {".docx", ".doc"}:
        from vs_app.integrations.files.docx_extractor import extract_docx

        result = extract_docx(file_bytes)
        return {"extraction_type": "docx", "result": result}

    raise ValueError(f"Unsupported file extension: {ext}")


def build_debug_payload(
    *,
    path: Path,
    ext: str,
    extraction: dict[str, Any],
    ocr_enabled: bool,
    max_slides: int,
    max_pages: int,
) -> dict[str, Any]:
    result = dict(extraction.get("result") or {})
    chunks = list(result.get("chunks") or [])
    consolidated_raw = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if isinstance(chunk, dict) and chunk.get("text") and not chunk.get("is_boilerplate")
    )
    consolidated_cleaned = clean_extracted_text(consolidated_raw)

    return {
        "filename": path.name,
        "path": str(path),
        "extension": ext.lstrip("."),
        "file_size_bytes": path.stat().st_size,
        "extraction_type": extraction.get("extraction_type"),
        "extraction_methods": extraction_methods(chunks),
        "options": {
            "ocr": ocr_enabled,
            "max_slides": max_slides,
            "max_pages": max_pages,
        },
        "summary": {
            "chunks": len(chunks),
            "has_tables": bool(result.get("has_tables")),
            "has_notes": bool(result.get("has_notes")),
            "ocr_used": bool(result.get("ocr_used")),
            "slide_count": result.get("slide_count"),
            "page_count": result.get("page_count"),
            "section_count": result.get("section_count"),
            "consolidated_word_count": len(consolidated_cleaned.split()),
            "consolidated_chars": len(consolidated_cleaned),
        },
        "chunks": chunks,
        "consolidated_cleaned_text": consolidated_cleaned,
        "raw_result": result,
    }


def extraction_methods(chunks: list[Any]) -> list[str]:
    methods: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        method = str(chunk.get("extraction_method") or "").strip()
        if method and method not in seen:
            seen.add(method)
            methods.append(method)
    return methods


def render_payload(payload: dict[str, Any], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if output_format == "text":
        return render_text(payload)
    return render_markdown(payload)


def render_markdown(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    chunks = list(payload.get("chunks") or [])
    lines: list[str] = [
        f"# Extraction Debug: {payload.get('filename')}",
        "",
        "## Summary",
        f"- Extension: {payload.get('extension')}",
        f"- Extraction type: {payload.get('extraction_type')}",
        f"- Extraction methods: {', '.join(payload.get('extraction_methods') or []) or 'unknown'}",
        f"- File size bytes: {payload.get('file_size_bytes')}",
        f"- Chunks: {summary.get('chunks', 0)}",
        f"- Has tables: {str(bool(summary.get('has_tables'))).lower()}",
        f"- Has notes: {str(bool(summary.get('has_notes'))).lower()}",
        f"- OCR used: {str(bool(summary.get('ocr_used'))).lower()}",
    ]

    for key in ("slide_count", "page_count", "section_count"):
        if summary.get(key) is not None:
            lines.append(f"- {key}: {summary.get(key)}")

    lines.extend(
        [
            f"- Consolidated cleaned words: {summary.get('consolidated_word_count', 0)}",
            f"- Consolidated cleaned chars: {summary.get('consolidated_chars', 0)}",
            "",
            "## Chunks",
        ]
    )

    if not chunks:
        lines.append("")
        lines.append("_No chunks extracted._")

    for idx, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            continue
        metadata = chunk_metadata(chunk)
        lines.extend(
            [
                "",
                f"## Chunk {idx}",
                *[f"- {key}: {value}" for key, value in metadata.items()],
                "",
                "```text",
                str(chunk.get("text") or ""),
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "## Consolidated Cleaned Text",
            "",
            "```text",
            str(payload.get("consolidated_cleaned_text") or ""),
            "```",
        ]
    )
    return "\n".join(lines)


def render_text(payload: dict[str, Any]) -> str:
    summary = dict(payload.get("summary") or {})
    chunks = list(payload.get("chunks") or [])
    lines: list[str] = [
        f"Extraction Debug: {payload.get('filename')}",
        f"Extension: {payload.get('extension')}",
        f"Extraction type: {payload.get('extraction_type')}",
        f"Extraction methods: {', '.join(payload.get('extraction_methods') or []) or 'unknown'}",
        f"Chunks: {summary.get('chunks', 0)}",
        f"Has tables: {bool(summary.get('has_tables'))}",
        f"Has notes: {bool(summary.get('has_notes'))}",
        f"OCR used: {bool(summary.get('ocr_used'))}",
        "",
    ]

    for idx, chunk in enumerate(chunks, start=1):
        if not isinstance(chunk, dict):
            continue
        lines.append(f"--- Chunk {idx} ---")
        for key, value in chunk_metadata(chunk).items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append(str(chunk.get("text") or ""))
        lines.append("")

    lines.extend(
        [
            "=== Consolidated Cleaned Text ===",
            str(payload.get("consolidated_cleaned_text") or ""),
        ]
    )
    return "\n".join(lines)


def chunk_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}

    for key in ("chunk_id", "source", "slide_num", "page_num"):
        if key in chunk:
            metadata[key] = chunk.get(key)

    title = chunk.get("slide_title") or chunk.get("section_title")
    if title:
        metadata["title"] = title

    for key in ("word_count", "has_table", "has_notes"):
        if key in chunk:
            metadata[key] = chunk.get(key)

    if "is_boilerplate" in chunk:
        metadata["boilerplate"] = chunk.get("is_boilerplate")

    for key in ("weight_multiplier", "extraction_confidence", "extraction_method"):
        if key in chunk:
            metadata[key] = chunk.get(key)

    for key, value in chunk.items():
        if key in {
            "text",
            "chunk_id",
            "source",
            "slide_num",
            "page_num",
            "slide_title",
            "section_title",
            "word_count",
            "has_table",
            "has_notes",
            "is_boilerplate",
            "weight_multiplier",
            "extraction_confidence",
            "extraction_method",
        }:
            continue
        metadata[key] = value
    return metadata


def write_output(rendered: str, *, input_path: Path, out_dir: Path, output_format: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{input_path.stem}.extraction_debug{TEXT_SUFFIX[output_format]}"
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    raise SystemExit(main())
