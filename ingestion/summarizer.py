"""
LLM summarization: consolidated ticket text → TicketSummaryDocument.

This is the vocabulary bridge — converts operational idea-card language
into the structured fields used for VS prediction retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from clients.llm import complete_text
from content.schemas import TicketSummaryDocument

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 20_000
_MAX_OUTPUT_TOKENS = 1_200

_PROMPT = """\
You are an expert healthcare business analyst extracting a structured summary \
from a Jira idea card. This summary will be embedded for vector retrieval to \
match tickets to value streams — extract specific, discriminative vocabulary.

## TICKET
ID: {ticket_id}

## CONTENT
{text}

## INSTRUCTIONS
Extract a structured summary. Be precise and specific — generic phrases like \
"improve efficiency" are useless for retrieval. Use the exact domain language \
from the text.

Ignore project management details, timelines, team names, and Jira metadata.

Return ONLY valid JSON with exactly these keys:
{{
  "summary_text": "Dense summary covering: what change is proposed, what business \
problem it solves, who is affected, and what operational capabilities are involved. \
Include specific domain terms. No length limit — be thorough.",
  "business_problem": "The core pain point or gap. Be specific: 'Medicare members \
in IL cannot self-service plan comparisons during AEP' not 'members need better tools'.",
  "business_capability": "What process or capability is being built or changed. \
Use operational language: 'automated claims adjudication for out-of-network providers' \
not 'claims improvement'.",
  "key_terms": ["Extract 5-10 specific domain terms from the text that would help \
match this ticket to similar work. Include process names, system names, product \
names, regulatory references, and business-specific vocabulary."]
}}

No markdown fences. No extra text. Just the JSON object."""


def summarize_ticket(
    ticket_id: str,
    consolidated_text: str,
    llm_client: Any,
    cfg: Any,
) -> TicketSummaryDocument:
    """
    Call the LLM with the consolidated ticket text and return a
    TicketSummaryDocument with all fields populated except VS labels
    and embedding (those are set by the pipeline after this call).
    """
    if not consolidated_text.strip():
        logger.warning("Empty consolidated text for %s — returning minimal summary", ticket_id)
        return _empty_summary(ticket_id)

    prompt = _PROMPT.format(
        ticket_id=ticket_id,
        text=consolidated_text[:_MAX_INPUT_CHARS],
    )

    raw = _call_llm(prompt, llm_client, cfg)
    parsed = _parse_json(raw, ticket_id)

    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text=parsed.get("summary_text", ""),
        business_problem=parsed.get("business_problem", ""),
        business_capability=parsed.get("business_capability", ""),
        key_terms=_as_list(parsed.get("key_terms")),
    )


def _call_llm(prompt: str, llm_client: Any, cfg: Any) -> str:
    model = getattr(cfg, "llm_model", None) or "gpt-4o"
    try:
        return complete_text(
            prompt,
            llm_client,
            model=model,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return ""


def _parse_json(raw: str, ticket_id: str) -> dict:
    if not raw:
        return {}
    # Strip markdown fences if the model added them anyway
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed for %s — raw: %.200s", ticket_id, raw)
        return {}



def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value:
        return [value]
    return []


def _empty_summary(ticket_id: str) -> TicketSummaryDocument:
    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text="",
        business_problem="",
        business_capability="",
        key_terms=[],
    )
