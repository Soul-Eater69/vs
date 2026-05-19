from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    args = parse_args()

    input_path = Path(args.file).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"File not found: {input_path}")

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        elements = extract_with_unstructured(
            input_path=input_path,
            strategy=args.strategy,
            infer_table_structure=args.infer_table_structure,
        )
    except ImportError:
        raise SystemExit(
            "Missing dependency: unstructured\n\n"
            "Install it first:\n"
            '  pip install "unstructured[all-docs]"\n\n'
            "Or try smaller extras:\n"
            '  pip install "unstructured[pdf,pptx,docx]"\n'
        )

    stem = input_path.stem

    json_path = out_dir / f"{stem}.unstructured.elements.json"
    md_path = out_dir / f"{stem}.unstructured.md"
    txt_path = out_dir / f"{stem}.unstructured.txt"

    element_dicts = [element_to_dict(el) for el in elements]

    json_path.write_text(
        json.dumps(element_dicts, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    md_path.write_text(
        render_markdown(input_path.name, element_dicts),
        encoding="utf-8",
    )

    txt_path.write_text(
        render_text(element_dicts),
        encoding="utf-8",
    )

    print("")
    print("Unstructured extraction complete")
    print(f"  input:       {input_path}")
    print(f"  elements:    {len(element_dicts)}")
    print(f"  total chars: {sum(len(str(e.get('text') or '')) for e in element_dicts)}")
    print("")
    print("Written:")
    print(f"  {json_path}")
    print(f"  {md_path}")
    print(f"  {txt_path}")

    print("")
    print("Element category counts:")
    counts: dict[str, int] = {}
    for e in element_dicts:
        cat = str(e.get("category") or "Unknown")
        counts[cat] = counts.get(cat, 0) + 1
    for cat, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {cat}: {count}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract PDF/PPT/PPTX/DOCX/etc. using Unstructured and write JSON/MD/TXT output."
    )
    parser.add_argument("file", help="Input file path")
    parser.add_argument(
        "--out",
        default="output/unstructured_debug",
        help="Output directory. Default: output/unstructured_debug",
    )
    parser.add_argument(
        "--strategy",
        default="auto",
        choices=["auto", "fast", "hi_res", "ocr_only"],
        help="Extraction strategy. For PDFs, try hi_res if layout/table extraction matters.",
    )
    parser.add_argument(
        "--infer-table-structure",
        action="store_true",
        help="Ask Unstructured to infer table structure when supported.",
    )
    return parser.parse_args()


def extract_with_unstructured(
    *,
    input_path: Path,
    strategy: str,
    infer_table_structure: bool,
):
    from unstructured.partition.auto import partition

    kwargs: dict[str, Any] = {
        "filename": str(input_path),
    }

    if strategy != "auto":
        kwargs["strategy"] = strategy

    if infer_table_structure:
        kwargs["infer_table_structure"] = True

    try:
        return partition(**kwargs)
    except TypeError:
        # Some file types / versions may not accept strategy or infer_table_structure.
        # Fall back to the simplest partition call.
        return partition(filename=str(input_path))


def element_to_dict(element: Any) -> dict[str, Any]:
    category = getattr(element, "category", None) or element.__class__.__name__
    text = str(getattr(element, "text", "") or "")

    metadata_obj = getattr(element, "metadata", None)
    metadata: dict[str, Any] = {}

    if metadata_obj is not None:
        if hasattr(metadata_obj, "to_dict"):
            metadata = metadata_obj.to_dict()
        else:
            metadata = dict(getattr(metadata_obj, "__dict__", {}) or {})

    return {
        "category": category,
        "text": text,
        "metadata": metadata,
        "element_type": element.__class__.__name__,
    }


def render_markdown(filename: str, elements: list[dict[str, Any]]) -> str:
    lines: list[str] = []

    lines.append(f"# Unstructured Extraction: {filename}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Elements: {len(elements)}")
    lines.append(f"- Total chars: {sum(len(str(e.get('text') or '')) for e in elements)}")
    lines.append("")

    counts: dict[str, int] = {}
    for e in elements:
        cat = str(e.get("category") or "Unknown")
        counts[cat] = counts.get(cat, 0) + 1

    lines.append("## Element Category Counts")
    for cat, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {cat}: {count}")

    lines.append("")
    lines.append("## Elements")

    for idx, e in enumerate(elements, start=1):
        category = e.get("category") or "Unknown"
        text = str(e.get("text") or "").strip()
        metadata = e.get("metadata") or {}

        page_number = metadata.get("page_number")
        filename_meta = metadata.get("filename")
        text_as_html = metadata.get("text_as_html")

        lines.append("")
        lines.append(f"### Element {idx}: {category}")

        if page_number is not None:
            lines.append(f"- page_number: {page_number}")
        if filename_meta:
            lines.append(f"- filename: {filename_meta}")

        if text:
            lines.append("")
            lines.append("```text")
            lines.append(text)
            lines.append("```")

        if text_as_html:
            lines.append("")
            lines.append("#### Table HTML / Structured Text")
            lines.append("```html")
            lines.append(str(text_as_html))
            lines.append("```")

    return "\n".join(lines).strip() + "\n"


def render_text(elements: list[dict[str, Any]]) -> str:
    parts: list[str] = []

    for e in elements:
        text = str(e.get("text") or "").strip()
        if not text:
            continue

        category = e.get("category") or "Unknown"
        metadata = e.get("metadata") or {}
        page_number = metadata.get("page_number")

        header = f"[{category}"
        if page_number is not None:
            header += f" | page {page_number}"
        header += "]"

        parts.append(f"{header}\n{text}")

    return "\n\n".join(parts).strip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())