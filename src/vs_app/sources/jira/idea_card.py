"""Idea-card-first attachment selection for the Jira source layer.

When an IDMT ticket has an idea-card attachment, it is the sole authoritative
processing input. Otherwise we fall back to the top-N attachments. Pure selection
logic over already-fetched attachment metadata — no network, no client.
"""

from __future__ import annotations

from typing import Any

# Idea-card naming conventions (matched case-insensitively as substrings of the
# attachment filename / name / title). Deliberately narrow: a bare "idea" never
# matches — it must be paired with the card convention.
_IDEA_CARD_PATTERNS = ("idea card", "ideacard", "idea_card", "idea-card")


def is_idea_card_attachment(attachment: Any) -> bool:
    """True if the attachment looks like an idea card by filename/name/title."""
    identity = _attachment_identity(attachment).lower()
    return any(pattern in identity for pattern in _IDEA_CARD_PATTERNS)


def select_idea_card_attachment(attachments: Any) -> Any | None:
    """Return the first idea-card attachment, or ``None`` if none is present."""
    for attachment in attachments or []:
        if is_idea_card_attachment(attachment):
            return attachment
    return None


def select_processing_attachments(
    attachments: Any,
    *,
    fallback_limit: int = 4,
) -> tuple[list[Any], list[str]]:
    """Choose which attachments to process; returns (selected, warnings).

    Idea-card-first: if an idea-card attachment exists, return only it. Otherwise
    return the top ``fallback_limit`` attachments (with a warning when truncated).
    """
    items = list(attachments or [])
    warnings: list[str] = []

    idea_card = select_idea_card_attachment(items)
    if idea_card is not None:
        return [idea_card], warnings

    selected = items[: max(0, int(fallback_limit))]
    if len(items) > len(selected):
        warnings.append(
            f"no idea card found; using top {fallback_limit} of {len(items)} attachments"
        )
    return selected, warnings


def _attachment_identity(attachment: Any) -> str:
    """Filename/name/title text for an attachment (dict- or object-style)."""
    if isinstance(attachment, dict):
        parts = [attachment.get("filename"), attachment.get("name"), attachment.get("title")]
    else:
        parts = [
            getattr(attachment, "filename", None),
            getattr(attachment, "name", None),
            getattr(attachment, "title", None),
        ]
    return " ".join(str(part) for part in parts if part)


__all__ = [
    "is_idea_card_attachment",
    "select_idea_card_attachment",
    "select_processing_attachments",
]
