"""Section grouping helpers for chunk ingestion."""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_SECTION_HEADER_RE = re.compile(r"^[A-Z][^.?!,;]{0,60}$")


def build_section_chunks(slide_chunks: list[dict]) -> list[dict]:
    content_slides = [chunk for chunk in slide_chunks if not chunk.get("is_boilerplate")]
    if not content_slides:
        return []

    sections: list[dict] = []
    current: dict = {"title": None, "chunks": [], "start_slide": None}

    for chunk in content_slides:
        title = chunk.get("slide_title") or chunk.get("section_title") or ""
        layout = chunk.get("layout_name", "")

        is_new_section = (
            (title and _SECTION_HEADER_RE.match(title) and len(title.split()) <= 8)
            or layout == "Section Header"
            or _is_agenda_boundary(title)
        )

        if is_new_section and current["chunks"]:
            sections.append(_finalize_section(current))
            current = {
                "title": title or None,
                "chunks": [],
                "start_slide": _get_slide_num(chunk),
            }

        if current["title"] is None and title:
            current["title"] = title
        if current["start_slide"] is None:
            current["start_slide"] = _get_slide_num(chunk)

        current["chunks"].append(chunk)

    if current["chunks"]:
        sections.append(_finalize_section(current))

    if len(sections) <= 1 and len(content_slides) >= 8:
        sections = _fallback_sections(content_slides)

    return sections


def _finalize_section(section: dict) -> dict:
    chunks = section["chunks"]
    title = section["title"] or "Untitled Section"
    merged_lines: list[str] = [f"## Section: {title}"]

    for chunk in chunks:
        slide_text = chunk.get("text", "").strip()
        if slide_text:
            merged_lines.append(slide_text)

    merged_text = "\n\n".join(merged_lines)
    last_slide_num = _get_slide_num(chunks[-1])
    start = section["start_slide"] or _get_slide_num(chunks[0])

    is_pdf = any(chunk.get("source") == "pdf_page" for chunk in chunks)
    source_format = "pdf" if is_pdf else "pptx"

    return {
        "chunk_id": f"section-{start}",
        "source": "section",
        "source_format": source_format,
        "text": merged_text,
        "section_title": title,
        "page_range": [start, last_slide_num] if is_pdf else None,
        "slide_range": None if is_pdf else [start, last_slide_num],
        "child_count": len(chunks),
        "word_count": len(merged_text.split()),
        "is_boilerplate": False,
        "weight_multiplier": 1.0,
        "extraction_confidence": min(
            [chunk.get("extraction_confidence", 1.0) for chunk in chunks]
        ),
        "extraction_method": "section_grouping",
    }


def _fallback_sections(slides: list[dict], group_size: int = 4) -> list[dict]:
    sections: list[dict] = []
    for idx in range(0, len(slides), group_size):
        group = slides[idx : idx + group_size]
        title = group[0].get("slide_title") or f"Section {idx // group_size + 1}"
        section = {
            "title": title,
            "chunks": group,
            "start_slide": _get_slide_num(group[0]),
        }
        sections.append(_finalize_section(section))
    return sections


def _is_agenda_boundary(title: str) -> bool:
    agenda_re = re.compile(
        r"^(agenda|outline|overview|introduction|background|problem|solution|"
        r"approach|implementation|timeline|next steps|conclusion|summary|appendix)",
        re.IGNORECASE,
    )
    return bool(title and agenda_re.match(title.strip()))


def _get_slide_num(chunk: dict) -> Optional[int]:
    return chunk.get("slide_num") or chunk.get("page_num")


__all__ = ["build_section_chunks"]
