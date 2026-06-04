"""Validation for runtime stage generation.

Match predicted stages against the allowed stage dropdown for a Value Stream.
Case/space-insensitive, returning the catalog's canonical spelling so invented
stages are dropped and valid ones are normalized to their approved form.
"""

from __future__ import annotations


def match_allowed_stage(stage_name: str, allowed_stages: list[str]) -> str | None:
    """Return the allowed stage's canonical spelling, or ``None`` if not allowed."""
    target = _norm(stage_name)
    if not target:
        return None
    for allowed in allowed_stages or []:
        if _norm(allowed) == target:
            return str(allowed).strip()
    return None


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


__all__ = ["match_allowed_stage"]
