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
_CONTENT_FILTER_MARKERS = (
    "content filter",
    "content_filter",
    "response was filtered",
    "prompt triggering",
)
_PROMPT_FILTER_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(sexual assault|rape|molestation|human trafficking|domestic violence|"
            r"child abuse|elder abuse|abuse|assault)\b",
            re.IGNORECASE,
        ),
        "safety-sensitive care topic",
    ),
    (
        re.compile(
            r"\b(suicide|suicidal|self[- ]?harm|overdose|opioid overdose)\b",
            re.IGNORECASE,
        ),
        "behavioral health safety topic",
    ),
    (
        re.compile(
            r"\b(kill(?:ed|ing)?|murder|homicide|death|dead|fatal(?:ity|ities)?)\b",
            re.IGNORECASE,
        ),
        "mortality or severe-harm topic",
    ),
    (
        re.compile(
            r"\b(sex|sexual|pregnancy|pregnant|maternity|reproductive)\b",
            re.IGNORECASE,
        ),
        "family health",
    ),
)


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
        raw = _call_llm(prompt, llm_client, cfg)
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
        raw = _call_llm(prompt, llm_client, cfg)
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


def _call_llm(
    prompt: str,
    llm_client: Any,
    cfg: Any,
    error_cls: type[RuntimeError] = SummaryExtractionError,
) -> str:
    model = getattr(cfg, "llm_model", None) or "gpt-4o"
    try:
        raw = complete_text(
            prompt,
            llm_client,
            model=model,
            max_output_tokens=_output_token_limit(cfg),
            temperature=0.2,
        )
    except Exception as exc:
        raise error_cls(f"LLM call failed: {exc}") from exc

    if not str(raw or "").strip():
        raise error_cls("LLM returned empty response")
    return raw


def _input_char_limit(cfg: Any, attr: str) -> int:
    value = getattr(cfg, attr, _MAX_INPUT_CHARS)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = _MAX_INPUT_CHARS
    return max(1_000, limit)


def _output_token_limit(cfg: Any) -> int | None:
    value = getattr(cfg, "llm_max_output_tokens", _MAX_OUTPUT_TOKENS)
    if value is None:
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = _MAX_OUTPUT_TOKENS
    return max(200, limit)


def _should_retry_with_sanitized_prompt(exc: Exception, cfg: Any) -> bool:
    if not bool(getattr(cfg, "enable_llm_prompt_sanitization_retry", True)):
        return False
    text = str(exc).lower()
    return any(marker in text for marker in _CONTENT_FILTER_MARKERS)


def sanitize_for_llm_prompt(text: str) -> str:
    """Redact safety-filter-prone clinical wording while preserving business context."""
    sanitized = str(text or "")
    for pattern, replacement in _PROMPT_FILTER_REPLACEMENTS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized

def _parse_json(raw: str, ticket_id: str) -> dict:
    return parse_structured_summary_payload(raw, context_id=ticket_id, logger=logger)


def _validate(
    model_cls,
    payload: dict,
    context_id: str,
    error_cls: type[RuntimeError],
):
    """Validate a parsed dict against a Pydantic model."""
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise error_cls(
            f"{model_cls.__name__} validation failed for {context_id}: {exc}"
        ) from exc


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
