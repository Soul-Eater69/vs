"""LLM-backed summary extraction and value-stream classification."""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from vs_app.integrations.llm.client import complete_text
from vs_app.modules.prompts.schemas import SummaryOutput, VsClassificationResult
from vs_app.modules.tickets.documents import TicketSummaryDocument

from .mapper import parse_structured_summary_payload
from .prompt_builder import (
    build_structured_summary_prompt,
    build_value_stream_classification_prompt,
)

logger = logging.getLogger(__name__)

_MAX_INPUT_CHARS = 20_000
_MAX_OUTPUT_TOKENS = 1_200


def summarize_ticket(
    ticket_id: str,
    consolidated_text: str,
    llm_client: Any,
    cfg: Any,
) -> TicketSummaryDocument:
    """Call the LLM and return a TicketSummaryDocument."""
    if not consolidated_text.strip():
        logger.warning("Empty consolidated text for %s - returning minimal summary", ticket_id)
        return _empty_summary(ticket_id)

    prompt = build_structured_summary_prompt(
        ticket_id=ticket_id,
        text=consolidated_text[:_MAX_INPUT_CHARS],
    )

    raw = _call_llm(prompt, llm_client, cfg)
    parsed = parse_structured_summary_payload(raw, context_id=ticket_id, logger=logger)
    output = _validate(SummaryOutput, parsed, ticket_id)

    return TicketSummaryDocument(
        ticket_id=ticket_id,
        summary_text=output.summary_text,
        business_problem=output.business_problem,
        business_capability=output.business_capability,
        key_terms=output.key_terms,
        stakeholders=output.stakeholders,
        systems_and_products=output.systems_and_products,
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
    """Add per-value-stream provenance (inference_type: direct | implied)."""
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

    prompt = build_value_stream_classification_prompt(
        ticket_id=ticket_id,
        text=consolidated_text[:_MAX_INPUT_CHARS],
        value_streams="\n".join(f"- {name}" for name in normalized_names),
    )
    raw = _call_llm(prompt, llm_client, cfg)
    parsed = _parse_json(raw, f"{ticket_id}:value_streams")
    output = _validate(VsClassificationResult, parsed, f"{ticket_id}:value_streams")

    name_to_id = {
        name.lower(): normalized_ids[idx] if idx < len(normalized_ids) else ""
        for idx, name in enumerate(normalized_names)
    }
    matched: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in output.value_streams:
        matched_name = _match_known_value_stream(item.vs_name, normalized_names)
        if not matched_name:
            continue
        key = matched_name.lower()
        if key in seen:
            continue

        reason = item.reason.strip() or _fallback_reason(label_source)

        matched.append(
            {
                "vs_id": name_to_id.get(key, ""),
                "vs_name": matched_name,
                "inference_type": item.inference_type,
                "reason": reason,
            }
        )
        seen.add(key)

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


def _validate(model_cls, payload: dict, context_id: str):
    """Validate a parsed dict against a Pydantic model; on failure, return defaults."""
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        logger.warning("%s output failed schema validation for %s: %s", model_cls.__name__, context_id, exc)
        return model_cls()


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


__all__ = ["classify_ticket_value_streams", "summarize_ticket"]
