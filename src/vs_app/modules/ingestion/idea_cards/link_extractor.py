"""Extract idea-card links and statements from ticket descriptions."""
from __future__ import annotations

import re
import uuid
from urllib.parse import urlparse

from .models import LinkCandidate

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_STATEMENT_PATTERNS = [
    re.compile(r"idea\s+card\s*:\s*https?://\S+", re.IGNORECASE),
    re.compile(r"idea\s+card\s+attached", re.IGNORECASE),
    re.compile(r"see\s+attached\s+idea\s+card", re.IGNORECASE),
    re.compile(r"business\s+case\s+attached", re.IGNORECASE),
    re.compile(r"proposal\s+(?:is\s+)?attached", re.IGNORECASE),
    re.compile(r"deck\s+(?:is\s+)?attached", re.IGNORECASE),
    re.compile(r"initiative\s+(?:document\s+)?attached", re.IGNORECASE),
]

_IDEA_LINK_CONTEXT_PATTERNS = [
    re.compile(r"idea\s+card", re.IGNORECASE),
    re.compile(r"business\s+case", re.IGNORECASE),
    re.compile(r"\bproposal\b", re.IGNORECASE),
    re.compile(r"\bdeck\b", re.IGNORECASE),
    re.compile(r"\binitiative\b", re.IGNORECASE),
    re.compile(r"\bpitch\b", re.IGNORECASE),
]

_INLINE_IDEA_CARD_LINK = re.compile(r"idea\s+card\s*:\s*(https?://\S+)", re.IGNORECASE)

CONTEXT_WINDOW = 150


def extract_description_candidates(
    description: str,
) -> tuple[list[LinkCandidate], list[str]]:
    """
    Extract link candidates and idea-card statement strings from a description.
    Returns (links, statements).
    """
    if not description:
        return [], []

    statements: list[str] = []
    link_candidates: list[LinkCandidate] = []
    seen_urls: set[str] = set()

    for pattern in _STATEMENT_PATTERNS:
        for match in pattern.finditer(description):
            stmt = match.group(0).strip()
            if stmt not in statements:
                statements.append(stmt)

    explicit_idea_urls: set[str] = set()
    for match in _INLINE_IDEA_CARD_LINK.finditer(description):
        url = match.group(1).rstrip(".,;:!?)")
        explicit_idea_urls.add(url)

    for url_match in URL_RE.finditer(description):
        url = url_match.group(0).rstrip(".,;:!?)")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        start = url_match.start()
        end = url_match.end()
        ctx_start = max(0, start - CONTEXT_WINDOW)
        ctx_end = min(len(description), end + CONTEXT_WINDOW)
        context = description[ctx_start:ctx_end]

        mentioned_as_idea_card = (
            url in explicit_idea_urls
            or any(p.search(context) for p in _IDEA_LINK_CONTEXT_PATTERNS)
        )

        parsed = urlparse(url)
        host = parsed.netloc or ""
        path_hint = (parsed.path or "")[:80]

        link_candidates.append(
            LinkCandidate(
                id=f"link-{uuid.uuid4().hex[:8]}",
                url=url,
                url_host=host,
                url_path_hint=path_hint,
                raw_context=context,
                mentioned_as_idea_card=mentioned_as_idea_card,
            )
        )

    return link_candidates, statements
