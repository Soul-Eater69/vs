"""
Chunking strategy - Section 8 of the architecture spec.

Implements:
  - Section-level chunk assembly (8.2)  [V2]
  - Slide/page chunks inherit from extraction modules
  - Supplementary content chunks (8.6)
"""

from __future__ import annotations
import logging
import re

logger = logging.getLogger(__name__)

# A section boundary is detected when a slide title looks like a header:
# short, no sentence punctuation, starts with a capital letter.
_SECTION_HEADER_RE = re.compile(r"^[A-Z][^.?!,;]{0,60}$")


def build_section_chunks(slide_chunks: list[dict]) -> list[dict]:
    """
    Group consecutive slide/page chunks into logical section chunks.
    Requires at least SECTION_MIN_SLIDES non-boilerplate slides.

    Returns a list of section-level chunk dicts.
    """
    # Filter to only non-boilerplate slides
    content_slides = [c for c in slide_chunks if not c.get("is_boilerplate")]
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

    # If section detection produced just one giant section, use the fallback grouper
    if len(sections) <= 1 and len(content_slides) >= 8:
        sections = _fallback_sections(content_slides)

    return sections


def _finalize_section(section: dict) -> dict:
    chunks = section["chunks"]
    title = section["title"] or "Untitled Section"
    merged_lines: list[str] = [f"## Section: {title}"]

    for c in chunks:
        slide_text = c.get("text", "").strip()
        if slide_text:
            merged_lines.append(slide_text)

    merged_text = "\n\n".join(merged_lines)
    last_slide_num = _get_slide_num(chunks[-1])
    start = section["start_slide"] or _get_slide_num(chunks[0])

    # Detect whether children are PDF pages or PPTX slides
    is_pdf = any(c.get("source") == "pdf_page" for c in chunks)
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
            [c.get("extraction_confidence", 1.0) for c in chunks]
        ),
        "extraction_method": "section_grouping",
    }


def _fallback_sections(slides: list[dict], group_size: int = 4) -> list[dict]:
    """Group slides into equal-sized sections when structure detection fails."""
    sections: list[dict] = []
    for i in range(0, len(slides), group_size):
        group = slides[i : i + group_size]
        title = group[0].get("slide_title") or f"Section {i // group_size + 1}"
        section = {
            "title": title,
            "chunks": group,
            "start_slide": _get_slide_num(group[0]),
        }
        sections.append(_finalize_section(section))
    return sections


def _is_agenda_boundary(title: str) -> bool:
    """Detect agenda-style section titles."""
    agenda_re = re.compile(
        r"^(agenda|outline|overview|introduction|background|problem|solution|"
        r"approach|implementation|timeline|next steps|conclusion|summary|appendix)",
        re.IGNORECASE,
    )
    return bool(title and agenda_re.match(title.strip()))


def _get_slide_num(chunk: dict) -> Optional[int]:
    return chunk.get("slide_num") or chunk.get("page_num")
