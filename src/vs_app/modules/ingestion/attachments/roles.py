"""Attachment document role helpers."""

from __future__ import annotations


def normalize_doc_role(att: dict) -> str:
    role = str(att.get("doc_role") or "").strip()
    if role == "supporting":
        return "supporting_doc"
    if role in {"primary_idea_card", "primary_fallback"}:
        return role
    return "supporting_doc"


def doc_role_weight(doc_role: str) -> float:
    if doc_role == "primary_idea_card":
        return 1.0
    if doc_role == "primary_fallback":
        return 0.9
    if doc_role == "supporting_doc":
        return 0.72
    return 0.5


__all__ = ["doc_role_weight", "normalize_doc_role"]
