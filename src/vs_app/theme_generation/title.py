"""Deterministic theme-title construction.

Business rule: a theme title is the IDMT ticket title joined with the Value
Stream name — no LLM, no prompt, no Jira call. This keeps titles stable and free.
"""

from __future__ import annotations


def build_theme_title(*, idmt_title: str = "", value_stream_name: str = "") -> str:
    """Build ``"{idmt_title} - {value_stream_name}"`` deterministically.

    Falls back to whichever part is present; returns ``""`` when both are blank.
    """
    idmt = _clean(idmt_title)
    value_stream = _clean(value_stream_name)
    if idmt and value_stream:
        return f"{idmt} - {value_stream}"
    return idmt or value_stream


def _clean(value: str) -> str:
    return " ".join(str(value or "").split())


__all__ = ["build_theme_title"]
