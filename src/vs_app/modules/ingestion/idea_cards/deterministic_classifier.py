"""Fuzzy/metadata-only idea-card presence classifier."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import AttachmentCandidate, FuzzyDecision, LinkAccessStatus, LinkCandidate

_STRONG_IDEA_RE = re.compile(
    r"\b(idea\s*card|initiative|proposal|business\s*case|pitch\s*deck)\b",
    re.IGNORECASE,
)
_DECK_RE = re.compile(
    r"\b(deck|proposal|business\s*case|brief|overview|roadmap|strategy)\b",
    re.IGNORECASE,
)
_DOCUMENT_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".docx", ".doc"}

STRONG_IDEA_EXTENSIONS = {"pptx", "ppt", "pdf", "docx"}

_INACCESSIBLE_STATUSES = {
    LinkAccessStatus.auth_required,
    LinkAccessStatus.forbidden,
    LinkAccessStatus.sharepoint_auth_required,
    LinkAccessStatus.timeout,
    LinkAccessStatus.connection_error,
    LinkAccessStatus.not_found,
    LinkAccessStatus.unknown_error,
}


def _filename_strong(filename: str) -> bool:
    return bool(_STRONG_IDEA_RE.search(filename))


def _filename_deck(filename: str) -> bool:
    return bool(_DECK_RE.search(filename))


def _assess_link_usefulness(link: LinkCandidate) -> tuple[bool, str]:
    """Determine fuzzy usefulness of a link from metadata only."""
    if link.mentioned_as_idea_card:
        return True, "URL is explicitly labeled as the idea card in context"
    # Check URL path for document extension
    path = urlparse(link.url).path.lower()
    if any(path.endswith(ext) for ext in _DOCUMENT_EXTENSIONS):
        return True, "URL path points to a document file"
    if _STRONG_IDEA_RE.search(link.raw_context):
        return True, "URL context contains idea-card keywords"
    return False, "URL context does not suggest idea-card relevance"


def classify_fuzzy(
    description_statements: list[str],
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
) -> FuzzyDecision:
    """
    Classify idea-card presence using metadata-only heuristics.

    Side effects: sets is_useful_fuzzy and usefulness_reason_fuzzy on each LinkCandidate.
    Returns FuzzyDecision.
    """
    # --- Assess link usefulness (mutates in place) ---
    for lnk in links:
        useful, reason = _assess_link_usefulness(lnk)
        lnk.is_useful_fuzzy = useful
        lnk.usefulness_reason_fuzzy = reason

    has_statements = bool(description_statements)

    idea_links = [lnk for lnk in links if lnk.mentioned_as_idea_card]
    accessible_idea_links = [
        lnk for lnk in idea_links
        if lnk.access_status == LinkAccessStatus.accessible
    ]
    inaccessible_idea_links = [
        lnk for lnk in idea_links
        if lnk.access_status in _INACCESSIBLE_STATUSES
    ]
    sharepoint_idea_links = [
        lnk for lnk in idea_links
        if lnk.access_status == LinkAccessStatus.sharepoint_auth_required
    ]

    strong_atts = [
        att for att in attachments
        if att.extension in STRONG_IDEA_EXTENSIONS
        and (
            _filename_strong(att.filename)
            or att.mentioned_as_idea_card
            or att.fuzzy_score >= 40
        )
    ]
    deck_atts = [
        att for att in attachments
        if att.extension in STRONG_IDEA_EXTENSIONS
        and (
            _filename_deck(att.filename)
            or att.fuzzy_score >= 20
        )
        and att not in strong_atts
    ]

    # --- CONFIRMED ---
    if accessible_idea_links:
        lnk = accessible_idea_links[0]
        return _decision("confirmed", 0.95, "link", lnk.id, lnk.url, False, [
            "Explicit idea-card link is accessible",
        ])

    if strong_atts and any(att.fuzzy_score >= 40 for att in strong_atts):
        best = max(strong_atts, key=lambda a: a.fuzzy_score)
        if best.fuzzy_score >= 50:
            return _decision("confirmed", 0.88, "attachment", best.id, best.filename, False, [
                "Attachment router confirmed primary idea card (high score)",
            ])

    # --- LIKELY ---
    if has_statements and strong_atts:
        best = strong_atts[0]
        return _decision("likely", 0.78, "attachment", best.id, best.filename, True, [
            "Description contains idea-card statement",
            f"Strong attachment present: {best.filename}",
        ])

    if sharepoint_idea_links:
        lnk = sharepoint_idea_links[0]
        return _decision("likely", 0.70, "link", lnk.id, lnk.url, True, [
            "SharePoint link explicitly mentioned as idea card (auth required)",
        ])

    if strong_atts:
        best = strong_atts[0]
        conf = 0.72 if _filename_strong(best.filename) else 0.65
        return _decision("likely", conf, "attachment", best.id, best.filename, True, [
            f"Attachment filename strongly matches idea-card patterns: {best.filename}",
        ])

    # --- POSSIBLE ---
    if deck_atts:
        best = deck_atts[0]
        return _decision("possible", 0.50, "attachment", best.id, best.filename, True, [
            f"Top attachment looks like a deck/proposal: {best.filename}",
        ])

    extractable = [att for att in attachments if att.extension in STRONG_IDEA_EXTENSIONS]
    if extractable and not has_statements and not idea_links:
        best = extractable[0]
        return _decision("possible", 0.40, "attachment", best.id, best.filename, True, [
            f"Extractable attachment present but no strong idea-card signal: {best.filename}",
        ])

    if has_statements and not extractable:
        return _decision("possible", 0.45, None, None, None, True, [
            "Description mentions idea card/proposal but no clear attachment found",
        ])

    # --- INACCESSIBLE ---
    if inaccessible_idea_links and not strong_atts and not deck_atts:
        lnk = inaccessible_idea_links[0]
        return _decision("inaccessible", 0.75, "link", lnk.id, lnk.url, True, [
            f"Only strong evidence is an inaccessible link ({lnk.access_status.value})",
            "No usable attachment found",
        ])

    # --- AMBIGUOUS ---
    if len(strong_atts) > 1 or (strong_atts and deck_atts):
        best = strong_atts[0] if strong_atts else deck_atts[0]
        all_candidates = strong_atts + deck_atts
        return _decision("ambiguous", 0.45, "attachment", best.id, best.filename, True, [
            f"Multiple competing strong attachments ({len(all_candidates)}) — no clear primary",
        ])

    # --- ABSENT ---
    return _decision("absent", 0.85, None, None, None, False, [
        "No useful links, no idea-card statements, no relevant attachments",
    ])


def _decision(
    presence: str,
    confidence: float,
    source_type: str | None,
    source_id: str | None,
    source_name: str | None,
    needs_llm: bool,
    reasons: list[str],
) -> FuzzyDecision:
    return FuzzyDecision(
        presence=presence,
        confidence=confidence,
        primary_source_type=source_type,
        primary_source_id=source_id,
        primary_source_name=source_name,
        reasons=reasons,
        needs_llm_review=needs_llm,
    )
