from __future__ import annotations

import logging
import os
import re
from typing import Optional

from vs_app.modules.rag.query.retrieval_summary import (
    build_structured_summary_prompt,
    format_structured_summary_text,
    parse_structured_summary_payload,
)
from vs_app.shared.text_cleaning import clean_extracted_text

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
    for idx, (start, name) in enumerate(positions):
        end = positions[idx + 1][0] if idx + 1 < len(positions) else len(cleaned)
        sections[name] = cleaned[start:end].strip()
    return sections


def normalize_for_search(text: Optional[str], max_chars: int = 2500) -> str:
    cleaned = clean_opt_text(text or "")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9\n\.\,\/\-\&\$\+\ ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def condense_idea_card(raw_text: str, max_chars: int = 3500) -> str:
    from vs_app.integrations.clients.llm import IDPChatOpenAI

    cleaned = clean_opt_text(raw_text)
    prompt = build_structured_summary_prompt(ticket_id="QUERY", text=cleaned[:8000])
    model = os.environ.get("CONDENSE_LLM_MODEL", "gpt-5-mini-idp")
    reply = IDPChatOpenAI(model=model).invoke(input=prompt)
    parsed = parse_structured_summary_payload(
        getattr(reply, "content", "") or "",
        context_id="query_summary",
        logger=logger,
    )
    condensed = format_structured_summary_text(parsed, max_chars=max_chars)
    return condensed.replace("\\n", "\n").replace("\\r", "")
