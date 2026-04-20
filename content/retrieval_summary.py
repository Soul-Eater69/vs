from __future__ import annotations

import json
import re
from typing import Any, Mapping


_STRUCTURED_SUMMARY_SCHEMA = """{
  "summary_text": "Dense, specific summary of the proposed business change, the people or business areas affected, and the operational work involved.",
  "business_problem": "The concrete business pain point or gap this work addresses.",
  "business_capability": "The process, capability, workflow, or product capability being created or changed.",
  "key_terms": ["5-10 exact domain terms, system names, process names, product names, or regulatory references from the source text."]
}"""


def build_structured_summary_prompt(*, ticket_id: str, text: str) -> str:
    return f"""\
You are a senior healthcare business analyst preparing a retrieval summary for a Jira idea card.

This structured summary is used in two places:
1. to index historical Jira tickets for semantic retrieval
2. to summarize a live idea-card query before retrieval

Write for retrieval quality, not for executive polish.

Rules:
- Preserve concrete business language from the source text.
- Be specific about products, channels, workflows, populations, capabilities, systems, and healthcare domains.
- Avoid generic filler like "improve efficiency", "enhance experience", or "support transformation" unless the source gives concrete detail.
- Ignore project management noise, timelines, owner names, Jira mechanics, ticket bookkeeping, and repeated slide boilerplate.
- Do not infer value stream labels or taxonomy names unless the source text explicitly says them.
- Prefer operational wording over abstract summaries.

## TICKET
ID: {ticket_id}

## CONTENT
{text}

Return ONLY valid JSON with exactly this shape:
{_STRUCTURED_SUMMARY_SCHEMA}

No markdown fences. No commentary. Just the JSON object."""


def parse_structured_summary_payload(
    raw: str,
    *,
    context_id: str = "summary",
    logger: Any | None = None,
) -> dict[str, Any]:
    if not raw:
        return {}

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        if logger is not None:
            logger.warning("JSON parse failed for %s — raw: %.200s", context_id, raw)
        return {}

    return payload if isinstance(payload, dict) else {}


def coerce_key_terms(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def format_structured_summary_text(
    payload: Mapping[str, Any] | Any,
    *,
    max_chars: int | None = None,
) -> str:
    summary_text = _field(payload, "summary_text")
    business_problem = _field(payload, "business_problem")
    business_capability = _field(payload, "business_capability")
    key_terms = coerce_key_terms(_field(payload, "key_terms", raw=True))

    parts: list[str] = []
    if summary_text:
        parts.append(summary_text)
    if business_problem:
        parts.append(f"Problem: {business_problem}")
    if business_capability:
        parts.append(f"Capability: {business_capability}")
    if key_terms:
        parts.append("Key Terms: " + ", ".join(key_terms))

    text = "\n".join(part for part in parts if part).strip()
    if max_chars is not None and max_chars > 0:
        return text[:max_chars]
    return text


def _field(payload: Mapping[str, Any] | Any, name: str, *, raw: bool = False) -> Any:
    value: Any
    if isinstance(payload, Mapping):
        value = payload.get(name)
    else:
        value = getattr(payload, name, None)

    if raw:
        return value
    return str(value or "").strip()
