"""LLM-backed summary extraction and value-stream classification.

Shared LLM plumbing (the single call, limits, sanitization/retry, JSON parse,
validation) lives in ``llm_io`` and is re-exported here for backwards
compatibility. Value-stream classification still lives here for now; it is
slated to move to ``ingestion/ground_truth`` in a later feature, at which point
it will import the same ``llm_io`` helpers.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from vs_app.modules.prompts.schemas import SummaryOutput, VsClassificationResult
from vs_app.modules.tickets.documents import TicketSummaryDocument

from .llm_io import (
    _call_llm,
    _input_char_limit,
    _output_token_limit,
    _parse_json,
    _should_retry_with_sanitized_prompt,
    _validate,
    sanitize_for_llm_prompt,
)
from .mapper import parse_structured_summary_payload
from .prompt_builder import (
    build_structured_summary_prompt,
    build_value_stream_classification_prompt,
)

logger = logging.getLogger(__name__)


class SummaryExtractionError(RuntimeError):
    pass


class ValueStreamClassificationError(RuntimeError):
    pass


def summarize_ticket(
    ticket_id: str,
    consolidated_text: str,
    llm_client: Any,
    cfg: Any,
) -> TicketSummaryDocument:
    """Call the LLM and return a TicketSummaryDocument."""
    if not consolidated_text.strip():
        raise SummaryExtractionError(f"Consolidated text is empty for {ticket_id}")

    prompt = build_structured_summary_prompt(
        ticket_id=ticket_id,
        text=consolidated_text[:_input_char_limit(cfg, "summary_input_char_limit")],
    )

    try:
        raw = _call_llm(prompt, llm_client, cfg, error_cls=SummaryExtractionError)
    except SummaryExtractionError as exc:
        if not _should_retry_with_sanitized_prompt(exc, cfg):
            raise
        sanitized_text = sanitize_for_llm_prompt(consolidated_text)
        if sanitized_text == consolidated_text:
            raise
        logger.warning(
            "Retrying %s summary with sanitized prompt after content-filter response",
            ticket_id,
        )
        prompt = build_structured_summary_prompt(
            ticket_id=ticket_id,
            text=sanitized_text[:_input_char_limit(cfg, "summary_input_char_limit")],
        )
        raw = _call_llm(prompt, llm_client, cfg, error_cls=SummaryExtractionError)
    parsed = parse_structured_summary_payload(raw, context_id=ticket_id, logger=logger)
    output = _validate(SummaryOutput, parsed, ticket_id, SummaryExtractionError)

    if not output.summary_text.strip():
        raise SummaryExtractionError(f"Missing summary_text for {ticket_id}")
    if not output.business_problem.strip():
        raise SummaryExtractionError(f"Missing business_problem for {ticket_id}")
    if not output.business_capability.strip():
        raise SummaryExtractionError(f"Missing business_capability for {ticket_id}")

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
    jira_group_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """Classify supplied value-stream labels as direct or implied.

    This does not discover new labels and does not default unresolved labels.
    Tickets should already have verified Jira value-stream issue-link labels.
    """
    normalized_names = [str(name).strip() for name in value_stream_names if str(name).strip()]
    normalized_ids = [str(vs_id).strip() for vs_id in value_stream_ids]
    normalized_group_ids = [str(group_id).strip() for group_id in (jira_group_ids or [])]
    if not normalized_names:
        return []

    if llm_client is None:
        raise ValueStreamClassificationError(
            f"LLM client required for direct/implied VS classification: {ticket_id}"
        )

    if not consolidated_text.strip():
        raise ValueStreamClassificationError(
            f"Consolidated text is empty; cannot classify direct/implied VS labels: {ticket_id}"
        )

    prompt = build_value_stream_classification_prompt(
        ticket_id=ticket_id,
        text=consolidated_text[:_input_char_limit(cfg, "classification_input_char_limit")],
        value_streams="\n".join(f"- {name}" for name in normalized_names),
    )
    try:
        raw = _call_llm(
            prompt,
            llm_client,
            cfg,
            error_cls=ValueStreamClassificationError,
        )
    except ValueStreamClassificationError as exc:
        if not _should_retry_with_sanitized_prompt(exc, cfg):
            raise
        sanitized_text = sanitize_for_llm_prompt(consolidated_text)
        if sanitized_text == consolidated_text:
            raise
        logger.warning(
            "Retrying %s value-stream classification with sanitized prompt after content-filter response",
            ticket_id,
        )
        prompt = build_value_stream_classification_prompt(
            ticket_id=ticket_id,
            text=sanitized_text[:_input_char_limit(cfg, "classification_input_char_limit")],
            value_streams="\n".join(f"- {name}" for name in normalized_names),
        )
        raw = _call_llm(
            prompt,
            llm_client,
            cfg,
            error_cls=ValueStreamClassificationError,
        )
    parsed = _parse_json(raw, f"{ticket_id}:value_streams")
    output = _validate(
        VsClassificationResult,
        parsed,
        f"{ticket_id}:value_streams",
        ValueStreamClassificationError,
    )

    if not output.value_streams:
        raise ValueStreamClassificationError(
            f"Direct/implied VS classification returned no rows for labeled ticket {ticket_id}"
        )

    name_to_id = {
        name.lower(): normalized_ids[idx] if idx < len(normalized_ids) else ""
        for idx, name in enumerate(normalized_names)
    }
    name_to_group_id = {
        name.lower(): normalized_group_ids[idx] if idx < len(normalized_group_ids) else ""
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

        reason = item.reason.strip() or "Classified from ticket text and verified Jira value-stream labels."

        matched.append(
            {
                "vs_id": name_to_id.get(key, ""),
                "vs_name": matched_name,
                "jira_group_id": name_to_group_id.get(key, ""),
                "inference_type": item.inference_type,
                "reason": reason,
            }
        )
        seen.add(key)

    classified_names = {_normalize_name(row["vs_name"]) for row in matched}
    expected_names = {_normalize_name(name) for name in normalized_names}
    missing = expected_names - classified_names

    if missing and getattr(cfg, "strict_value_stream_classification", True):
        raise ValueStreamClassificationError(
            f"Direct/implied classification missed labels for {ticket_id}: {sorted(missing)}"
        )

    return matched


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


__all__ = [
    "SummaryExtractionError",
    "ValueStreamClassificationError",
    "classify_ticket_value_streams",
    "sanitize_for_llm_prompt",
    "summarize_ticket",
]
