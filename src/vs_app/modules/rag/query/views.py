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


def normalize_for_search(text: Optional[str], max_chars: int = 2500) -> str:
    cleaned = clean_opt_text(text or "")
    cleaned = cleaned.lower()
    cleaned = re.sub(r"[^a-z0-9\n\.\,\/\-\&\$\+\ ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def condense_idea_card(raw_text: str, max_chars: int = 3500) -> str:
    from vs_app.integrations.clients.llm import IDPChatOpenAI, build_extra_body

    cleaned = clean_opt_text(raw_text)
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
