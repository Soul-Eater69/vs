"""Build AttachmentCandidate objects by wrapping the existing attachment router (metadata-only)."""
from __future__ import annotations

import logging
from typing import Any

from vs_app.modules.ingestion.attachments.router import route_attachments

from .models import AttachmentCandidate

logger = logging.getLogger(__name__)

_DECK_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _att_id(raw: dict[str, Any]) -> str:
    return str(raw.get("id") or raw.get("attachment_id") or raw.get("filename") or "")


def _usefulness_reason(
    fuzzy_score: float,
    fuzzy_role: str,
    idea_card_likeness: float,
    mentioned_as_idea_card: bool,
    is_useful: bool,
) -> str:
    if not is_useful:
        if fuzzy_score > 0:
            return f"Router score {fuzzy_score:.0f} below threshold for idea-card relevance"
        return "No router score or role signal — not considered a candidate"
    if mentioned_as_idea_card:
        return "Explicitly mentioned as idea card in description"
    if fuzzy_role == "primary_idea_card":
        return "Attachment router identified as primary idea card"
    if fuzzy_score >= 35:
        return f"High attachment router score ({fuzzy_score:.0f})"
    if idea_card_likeness >= 0.4:
        return "High idea-card likeness score from attachment router"
    return "Supporting candidate identified by attachment router"


def build_attachment_candidates(
    attachments: list[dict[str, Any]],
    ticket_summary: str = "",
    mentioned_as_idea_card_filenames: set[str] | None = None,
) -> list[AttachmentCandidate]:
    """
    Run route_attachments (metadata-only, no download_fn) and convert to AttachmentCandidate list.
    Sets fuzzy_score, fuzzy_role, fuzzy_reasons, is_useful_fuzzy, usefulness_reason_fuzzy.
    """
    if not attachments:
        return []

    mentioned = {m.lower() for m in (mentioned_as_idea_card_filenames or set())}

    try:
        _, _, _, artifact = route_attachments(
            attachments,
            ticket_summary=ticket_summary,
            download_fn=None,
        )
        per_scored: dict[str, dict[str, Any]] = {}
        for entry in artifact.get("per_attachment_scores") or []:
            if not entry:
                continue
            key = str(entry.get("attachment_id") or entry.get("filename") or "")
            if key:
                per_scored[key] = entry
    except Exception as exc:
        logger.warning("route_attachments failed: %s", exc)
        per_scored = {}

    candidates: list[AttachmentCandidate] = []
    seen: set[str] = set()

    for raw in attachments:
        att_id = _att_id(raw)
        if att_id in seen:
            continue
        seen.add(att_id)

        filename = str(raw.get("filename") or "")
        extension = _ext_of(filename)
        url = str(raw.get("content") or raw.get("url") or "") or None
        size_raw = raw.get("size")
        size = int(size_raw) if size_raw else None

        mentioned_as_idea_card = (
            filename.lower() in mentioned or bool(raw.get("mentioned_as_idea_card"))
        )

        scored = per_scored.get(att_id) or per_scored.get(filename)

        fuzzy_score = 0.0
        fuzzy_role = ""
        fuzzy_reasons: list[str] = []
        idea_card_likeness = 0.0
        is_useful_fuzzy = False

        if scored:
            fuzzy_score = float(scored.get("triage_score") or 0)
            fuzzy_role = str(scored.get("doc_role") or "")
            fuzzy_reasons = list(scored.get("triage_reasons") or [])
            scores = scored.get("scores") or {}
            idea_card_likeness = float(scores.get("idea_card_likeness") or 0)
            is_useful_fuzzy = bool(
                scored.get("confirmed")
                or fuzzy_role in ("primary_idea_card", "supporting")
                or idea_card_likeness >= 0.4
                or fuzzy_score >= 35
            )
        elif mentioned_as_idea_card:
            is_useful_fuzzy = True

        usefulness_reason_fuzzy = _usefulness_reason(
            fuzzy_score, fuzzy_role, idea_card_likeness, mentioned_as_idea_card, is_useful_fuzzy
        )

        candidates.append(
            AttachmentCandidate(
                id=att_id or filename,
                filename=filename,
                extension=extension,
                size=size,
                url=url,
                mentioned_as_idea_card=mentioned_as_idea_card,
                fuzzy_score=fuzzy_score,
                fuzzy_role=fuzzy_role,
                fuzzy_reasons=fuzzy_reasons,
                is_useful_fuzzy=is_useful_fuzzy,
                usefulness_reason_fuzzy=usefulness_reason_fuzzy,
            )
        )

    return candidates
