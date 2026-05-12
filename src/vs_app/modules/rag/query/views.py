from __future__ import annotations

from dataclasses import dataclass
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

_TITLE_HEADER_RE = re.compile(
    r"^\s*(?:idea\s+card\s+title|idmt\s+title|title|summary)\s*:\s*(.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PreparedIdeaCard:
    source_ticket_title: str
    cleaned_text: str
    condensed_text: str


def clean_opt_text(raw_text: str) -> str:
    return clean_extracted_text(raw_text)


def clean_ppt_text(raw_text: str) -> str:
    return clean_opt_text(raw_text)


def normalize_for_search(text: Optional[str], max_chars: int = 2500) -> str:
    cleaned = clean_opt_text(text or "")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9\n\.\,\/\-\&\$\+\ ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def clean_title_part(value: str | None) -> str:
    text = " ".join(str(value or "").split())
    return text.strip(" -\t\r\n")


def extract_source_ticket_title(raw_text: str | None, fallback: str | None = None) -> str:
    """Extract only the source IDMT/idea-card title, never value-stream labels."""
    text = str(raw_text or "")
    for raw_line in text.splitlines()[:60]:
        line = clean_title_part(raw_line)
        if not line:
            continue
        match = _TITLE_HEADER_RE.match(line)
        if match:
            title = clean_title_part(match.group(1))
            if title:
                return title

    for raw_line in text.splitlines()[:20]:
        line = clean_title_part(raw_line)
        if not line or ":" in line and len(line.split()) <= 2:
            continue
        if 2 <= len(line.split()) <= 18 and len(line) <= 160:
            return line

    return clean_title_part(fallback)


def prepare_idea_card_for_rag(
    raw_text: str,
    *,
    max_chars: int = 3500,
    fallback_title: str | None = None,
) -> PreparedIdeaCard:
    cleaned = clean_ppt_text(raw_text)
    condensed = condense_idea_card(raw_text, max_chars=max_chars)
    title = extract_source_ticket_title(raw_text, fallback=fallback_title)
    return PreparedIdeaCard(
        source_ticket_title=title,
        cleaned_text=cleaned,
        condensed_text=condensed,
    )


def condense_idea_card(raw_text: str, max_chars: int = 3500) -> str:
    from vs_app.integrations.clients.llm import IDPChatOpenAI, build_extra_body

    cleaned = clean_opt_text(raw_text)
    if len(cleaned) <= max_chars:
        return cleaned[:max_chars]

    prompt = build_structured_summary_prompt(ticket_id="QUERY", text=cleaned[:8000])
    model = os.environ.get("CONDENSE_LLM_MODEL", "gpt-5-mini-idp")
    kwargs = {"model": model}
    reasoning_effort = os.environ.get("CONDENSE_LLM_REASONING_EFFORT", "low")
    if reasoning_effort:
        kwargs["extra_body"] = build_extra_body(reasoning_effort=reasoning_effort)
    reply = IDPChatOpenAI(**kwargs).invoke(input=prompt)
    parsed = parse_structured_summary_payload(
        getattr(reply, "content", "") or "",
        context_id="query_summary",
        logger=logger,
    )
    condensed = format_structured_summary_text(parsed, max_chars=max_chars)
    return condensed.replace("\\n", "\n").replace("\\r", "")
