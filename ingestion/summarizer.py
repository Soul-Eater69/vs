"""
LLM summarization: consolidated ticket text -> TicketSummaryDocument.

This is the vocabulary bridge that converts operational Jira idea-card language
into the structured fields used for retrieval.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..clients.llm import complete_text
from ..content.retrieval_summary import (
    build_structured_summary_prompt,
    coerce_key_terms,
    parse_structured_summary_payload,
)
from ..content.schemas import TicketSummaryDocument

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 20_000
_MAX_OUTPUT_TOKENS = 1_200

_VS_CLASSIFICATION_PROMPT = """\
Your Role: Expert healthcare business analyst specializing in Jira idea triage
and healthcare value-stream classification.

Short basic instruction: Review a Jira idea card and classify each
already-attached value stream using only the ticket text.

What you should do:
You will receive:

a ticket ID: {ticket_id}
ticket content: {text}
a list of already-attached value streams: {value_streams}

For each supplied value stream, determine whether it is:

"direct": the ticket explicitly addresses that value-stream domain, problem,
workflow, or operational area
"implied": the value stream is relevant, but only indirectly, adjacently,
upstream, or downstream from what the ticket explicitly describes

Important rules:

Classify every supplied value stream, even if the ticket text is limited or
somewhat ambiguous
Do not invent, infer, or add new value streams beyond those in {value_streams}
Use only the ticket text as evidence
Do not use the fact that a value stream is attached as evidence that it is
direct; the attachment list is the set to classify, not proof of strength
Choose "direct" only when the ticket text clearly describes work, problems,
workflows, stakeholders, or outcomes that are central to that value stream
Use "implied" when the value stream is related but not explicitly central in
the ticket
When uncertain between "direct" and "implied", prefer "implied"
Keep each reason to exactly one sentence
Make each reason specific enough to reflect the ticket context, citing concrete
business context from the ticket text, but not overly broad or repetitive
Preserve the value stream name exactly as supplied

Your Goal: Produce a reliable classification of all attached value streams
based strictly on the Jira ticket text, minimizing overreach while still making
a determination for every supplied value stream.

Result:
Return ONLY valid JSON with exactly this structure:
{{
  "value_streams": [
    {{
      "vs_name": "One of the supplied value stream names exactly",
      "inference_type": "direct or implied",
      "reason": "One sentence explaining why this ticket is direct or implied for that value stream"
    }}
  ]
}}

Constraint:

No markdown fences
No extra commentary, explanation, headings, or notes
No additional keys beyond those shown
No omitted supplied value streams
No value streams that were not supplied
Output must be valid JSON only
If the ticket is ambiguous, still classify each supplied value stream using the
strongest text-supported judgment

Context:
You are evaluating only the relationship between the provided Jira ticket text
and the already-attached value streams.
You are not creating new value streams, redesigning taxonomy, or making product
recommendations.
Your task is classification only."""


def summarize_ticket(
    ticket_id: str,
    consolidated_text: str,
    llm_client: Any,
    cfg: Any,
) -> TicketSummaryDocument:
    """
    Call the LLM with the consolidated ticket text and return a
    TicketSummaryDocument with all fields populated except VS labels
    and embedding.
    """
    if not consolidated_text.strip():
        logger.warning("Empty consolidated text for %s - returning minimal summary", ticket_id)
        return _empty_summary(ticket_id)

    prompt = build_structured_summary_prompt(
        ticket_id=ticket_id,
        text=consolidated_text[:_MAX_INPUT_CHARS],
    )

    raw = _call_llm(prompt, llm_client, cfg)
    parsed = parse_structured_summary_payload(raw, context_id=ticket_id, logger=logger)

    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text=parsed.get("summary_text", ""),
        business_problem=parsed.get("business_problem", ""),
        business_capability=parsed.get("business_capability", ""),
        key_terms=coerce_key_terms(parsed.get("key_terms")),
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

    # Preserve Jira provenance for missed labels, but downgrade non-verified rows
    # to the fallback confidence level instead of treating them as direct evidence.
    for row in fallback_rows:
        if row["vs_name"].lower() in seen:
            continue
        patched = dict(row)
        patched["inference_type"] = _fallback_inference_type(label_source)
        patched["reason"] = (
            "Not classified by the LLM; preserved as lower-confidence Jira provenance."
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
    return parse_structured_summary_payload(raw, context_id=ticket_id, logger=logger)


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
    inference_type = _fallback_inference_type(label_source)
    for idx, name in enumerate(value_stream_names):
        rows.append(
            {
                "vs_id": value_stream_ids[idx] if idx < len(value_stream_ids) else "",
                "vs_name": name,
                "inference_type": inference_type,
                "reason": _fallback_reason(label_source),
            }
        )
    return rows


def _fallback_inference_type(label_source: str) -> str:
    if str(label_source or "").strip() == "jira_issuelinks":
        return "direct"
    return "implied"


def _empty_summary(ticket_id: str) -> TicketSummaryDocument:
    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text="",
        business_problem="",
        business_capability="",
        key_terms=[],
    )
