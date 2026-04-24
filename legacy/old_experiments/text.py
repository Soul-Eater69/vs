from __future__ import annotations

import logging
import re
from typing import Optional

from .clients.llm import IDPChatOpenAI
from .ingestion.application.content.retrieval_summary import (
    build_structured_summary_prompt,
    format_structured_summary_text,
    parse_structured_summary_payload,
)
from .ingestion.application.processing.extraction.text_cleaning import clean_extracted_text

logger = logging.getLogger(__name__)


def clean_opt_text(raw_text: str) -> str:
    return clean_extracted_text(raw_text)


def clean_ppt_text(raw_text: str) -> str:
    return clean_opt_text(raw_text)


_SECTION_NAMES = [
    "Idea Card Executive Summary",
    "Problem Statement/Market Opportunity",
    "Business Solution and Objectives",
    "Alternative Solutions",
    "Value Proposition & Key Metrics",
    "Interdependencies",
    "Estimated Costs",
    "Resources/Investments Needed for Business Case",
]

_SECTION_PATTERNS = [
    (
        re.compile(r"(?:idea\s+card\s+)?executive\s+summary", re.IGNORECASE),
        "Idea Card Executive Summary",
    ),
    (
        re.compile(
            r"problem\s+statement(?:\s*/\s*market\s+opportunity)?",
            re.IGNORECASE,
        ),
        "Problem Statement/Market Opportunity",
    ),
    (
        re.compile(
            r"business\s+solution(?:\s+and\s+objectives)?",
            re.IGNORECASE,
        ),
        "Business Solution and Objectives",
    ),
    (
        re.compile(r"alternative\s+solutions?", re.IGNORECASE),
        "Alternative Solutions",
    ),
    (
        re.compile(
            r"value\s+proposition(?:\s*[&/]\s*key\s+metrics)?",
            re.IGNORECASE,
        ),
        "Value Proposition & Key Metrics",
    ),
    (
        re.compile(r"interdependenc(?:ies|y)", re.IGNORECASE),
        "Interdependencies",
    ),
    (
        re.compile(r"estimated\s+costs?", re.IGNORECASE),
        "Estimated Costs",
    ),
    (
        re.compile(r"resources?\s*/?\s*investments?\s+needed", re.IGNORECASE),
        "Resources/Investments Needed for Business Case",
    ),
]


def extract_signal_sections(raw_text: str) -> dict[str, str]:
    cleaned = clean_opt_text(raw_text)
    lower = cleaned.lower()

    positions: list[tuple[int, str]] = []
    matched_names: set[str] = set()

    for name in _SECTION_NAMES:
        idx = lower.find(name.lower())
        if idx != -1:
            positions.append((idx, name))
            matched_names.add(name)

    for pattern, canonical_name in _SECTION_PATTERNS:
        if canonical_name in matched_names:
            continue
        match = pattern.search(cleaned)
        if match:
            positions.append((match.start(), canonical_name))
            matched_names.add(canonical_name)

    positions.sort()
    sections: dict[str, str] = {}
    for i, (start, name) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(cleaned)
        sections[name] = cleaned[start:end].strip()
    return sections


def normalize_for_search(text: Optional[str], max_chars: int = 2500) -> str:
    cleaned = clean_opt_text(text or "")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9\n\.\,\/\-\&\$\+\ ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


SIGNAL_TERMS = {
    "member",
    "provider",
    "employer",
    "patient",
    "care",
    "clinical",
    "product",
    "offering",
    "launch",
    "enroll",
    "enrollment",
    "billing",
    "invoice",
    "payment",
    "quote",
    "pricing",
    "network",
    "program",
    "engagement",
    "support",
    "workflow",
    "digital",
    "platform",
    "risk",
    "compliance",
    "authorization",
    "utilization",
    "claims",
    "appeal",
    "navigation",
    "coordination",
    "sales",
    "growth",
    "commercial",
    "pharmacy",
    "behavioral",
    "oncology",
    "maternal",
    "preventive",
    "grievance",
    "credentialing",
    "contracting",
    "actuarial",
    "underwriting",
    "dental",
    "vision",
    "telehealth",
    "population",
    "quality",
    "accreditation",
    "fraud",
    "abuse",
    "prior auth",
    "referral",
    "discharge",
    "readmission",
    "chronic",
    "acute",
    "ambulatory",
    "inpatient",
    "outpatient",
    "revenue",
    "cost",
    "efficiency",
    "experience",
    "satisfaction",
    "retention",
}

ACTION_TERMS = {
    "launch",
    "improve",
    "reduce",
    "increase",
    "streamline",
    "enable",
    "automate",
    "support",
    "expand",
    "build",
    "create",
    "optimize",
    "establish",
    "manage",
    "onboard",
    "configure",
    "fulfill",
    "issue",
    "resolve",
    "align",
    "execute",
}


def extract_signal_lines(text: str, max_lines: int = 60, max_chars: int = 12000) -> str:
    """
    Pull the most business-salient lines from the PPT text so the summary step
    sees dense signal instead of the whole noisy deck.
    """
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if len(line) < 4:
            continue
        lines.append(line)

    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()

    for idx, line in enumerate(lines):
        low = line.lower()
        if low in seen:
            continue
        seen.add(low)

        score = 0

        if len(line) < 80:
            score += 1

        if raw_starts_like_bullet(line):
            score += 2

        score += sum(2 for term in SIGNAL_TERMS if term in low)
        score += sum(1 for term in ACTION_TERMS if term in low)

        if re.search(r"\$\d+|\d+ ?%", line):
            score += 1

        if 20 <= len(line) <= 240 and score == 0:
            score = 1

        if len(line) > 240:
            score -= 1

        if score > 0:
            scored.append((score, idx, line))

    top = sorted(scored, key=lambda x: (x[0], x[1]))[-max_lines:]
    top = sorted(top, key=lambda x: x[1])

    out_lines: list[str] = []
    total = 0
    for _, _, line in top:
        if total + len(line) + 1 > max_chars:
            break
        out_lines.append(line)
        total += len(line) + 1

    return "\n".join(out_lines)


def raw_starts_like_bullet(line: str) -> bool:
    return bool(re.match(r"^(?:[-*]|\u2022|\d+\))", line))


def condense_idea_card(raw_text: str, max_chars: int = 3500) -> str:
    """Summarize noisy idea-card text into the same retrieval shape used by indexing."""
    cleaned = clean_opt_text(raw_text)
    input_text = cleaned[:8000]

    prompt = build_structured_summary_prompt(
        ticket_id="QUERY",
        text=input_text,
    )

    try:
        reply = IDPChatOpenAI(model="gpt-4o-mini-idp").invoke(input=prompt)
        parsed = parse_structured_summary_payload(
            getattr(reply, "content", "") or "",
            context_id="query_summary",
            logger=logger,
        )
        condensed = format_structured_summary_text(parsed, max_chars=max_chars)
        if condensed and len(condensed) > 50:
            condensed = condensed.replace("\\n", "\n").replace("\\r", "")
            return condensed
    except Exception as exc:
        logger.warning(
            "Idea card condensation failed, using signal-line fallback: %s",
            exc,
        )

    return extract_signal_lines(cleaned, max_lines=40, max_chars=max_chars)
