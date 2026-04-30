"""Hierarchy helpers for chunk parent/section resolution."""

from __future__ import annotations


def find_parent_uid(section_uid_by_start: dict[int, str], position: int) -> str:
    if not section_uid_by_start:
        return ""
    applicable = [start for start in section_uid_by_start if start <= position]
    if not applicable:
        return section_uid_by_start[min(section_uid_by_start)]
    return section_uid_by_start[max(applicable)]


def section_start(section: dict, fallback: int) -> int:
    page_range = section.get("page_range") or []
    slide_range = section.get("slide_range") or []
    if page_range:
        return int(page_range[0] or fallback)
    if slide_range:
        return int(slide_range[0] or fallback)
    return fallback


__all__ = ["find_parent_uid", "section_start"]
