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

from ..clients.llm import complete_text
from ..content.schemas import TicketSummaryDocument

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

_VS_CLASSIFICATION_PROMPT = """\
You are an expert healthcare business analyst reviewing a Jira idea card and a
set of already-attached value streams.

Your job is not to invent new value streams. Only classify the supplied value
streams using the ticket text.

## TICKET
ID: {ticket_id}

## CONTENT
{text}

## ATTACHED VALUE STREAMS
{value_streams}

## INSTRUCTIONS
For EACH attached value stream, classify it as:
- "direct": the ticket explicitly addresses this value-stream domain/problem
- "implied": the value stream is relevant but more upstream/downstream/adjacent

Return ONLY valid JSON with exactly this shape:
{{
  "value_streams": [
    {{
      "vs_name": "One of the supplied value stream names exactly",
      "inference_type": "direct or implied",
      "reason": "One sentence explaining why this ticket is direct or implied for that value stream"
    }}
  ]
}}

Do not add value streams that were not supplied. No markdown fences. No extra text."""


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


def classify_ticket_value_streams(
    ticket_id: str,
    consolidated_text: str,
    value_stream_ids: list[str],
    value_stream_names: list[str],
    label_source: str,
    llm_client: Any | None,
    cfg: Any,
) -> list[dict[str, str]]:
    """
    Add per-value-stream provenance for historical matching.

    label_source answers "where did this label come from in Jira?"
    inference_type answers "does the ticket text support this VS directly or only indirectly?"
    """
    normalized_names = [str(name).strip() for name in value_stream_names if str(name).strip()]
    normalized_ids = [str(vs_id).strip() for vs_id in value_stream_ids]
    fallback_rows = _fallback_value_stream_rows(
        value_stream_ids=normalized_ids,
        value_stream_names=normalized_names,
        label_source=label_source,
    )
    if not normalized_names:
        return fallback_rows

    if llm_client is None or not consolidated_text.strip():
        return fallback_rows

    prompt = _VS_CLASSIFICATION_PROMPT.format(
        ticket_id=ticket_id,
        text=consolidated_text[:_MAX_INPUT_CHARS],
        value_streams="\n".join(f"- {name}" for name in normalized_names),
    )
    raw = _call_llm(prompt, llm_client, cfg)
    parsed = _parse_json(raw, f"{ticket_id}:value_streams")

    name_to_id = {
        name.lower(): normalized_ids[idx] if idx < len(normalized_ids) else ""
        for idx, name in enumerate(normalized_names)
    }
    matched: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in parsed.get("value_streams") or []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("vs_name") or "").strip()
        matched_name = _match_known_value_stream(raw_name, normalized_names)
        if not matched_name:
            continue
        key = matched_name.lower()
        if key in seen:
            continue

        inference_type = str(item.get("inference_type") or "direct").strip().lower()
        if inference_type not in {"direct", "implied"}:
            inference_type = "direct"

        reason = str(item.get("reason") or "").strip()
        if not reason:
            reason = _fallback_reason(label_source)

        matched.append(
            {
                "vs_id": name_to_id.get(key, ""),
                "vs_name": matched_name,
                "inference_type": inference_type,
                "reason": reason,
            }
        )
        seen.add(key)

    # Preserve every Jira-attached VS even if the model missed one.
    for row in fallback_rows:
        if row["vs_name"].lower() in seen:
            continue
        patched = dict(row)
        patched["reason"] = (
            "Not classified by LLM; defaulted to direct while preserving Jira label provenance."
        )
        matched.append(patched)

    return matched or fallback_rows


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


def _match_known_value_stream(candidate: str, known_names: list[str]) -> str:
    candidate_key = _normalize_name(candidate)
    if not candidate_key:
        return ""
    for name in known_names:
        if _normalize_name(name) == candidate_key:
            return name
    return ""


def _normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _fallback_reason(label_source: str) -> str:
    if label_source == "jira_issuelinks":
        return "Resolved from direct Jira issue-link value stream labels."
    if label_source == "jira_themes_fallback":
        return "Resolved from linked Jira themes because no direct value-stream issue links were present."
    return f"Resolved from Jira label source '{label_source}'."


def _fallback_value_stream_rows(
    value_stream_ids: list[str],
    value_stream_names: list[str],
    label_source: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for idx, name in enumerate(value_stream_names):
        rows.append(
            {
                "vs_id": value_stream_ids[idx] if idx < len(value_stream_ids) else "",
                "vs_name": name,
                "inference_type": "direct",
                "reason": _fallback_reason(label_source),
            }
        )
    return rows


def _empty_summary(ticket_id: str) -> TicketSummaryDocument:
    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text="",
        business_problem="",
        business_capability="",
        key_terms=[],
    )
