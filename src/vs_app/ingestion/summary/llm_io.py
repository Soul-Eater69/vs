"""Shared LLM plumbing for ingestion summary workflows.

Low-level helpers used by both summary generation (``summarize_ticket``) and
value-stream classification (``classify_ticket_value_streams``): the single LLM
call, input/output limits, content-filter sanitization + retry detection, JSON
parsing, and Pydantic validation.

These are intentionally free of summary- or value-stream-specific logic so they
can be reused as those layers split apart (e.g. value-stream classification
moving to ``ingestion/ground_truth`` in a later feature). Callers pass the
``error_cls`` they want raised, so this module does not depend on any specific
error type.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import ValidationError

from vs_app.integrations.llm.client import complete_text

from .mapper import parse_structured_summary_payload

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


def _call_llm(
    prompt: str,
    llm_client: Any,
    cfg: Any,
    error_cls: type[RuntimeError] = RuntimeError,
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


__all__ = [
    "_call_llm",
    "_input_char_limit",
    "_output_token_limit",
    "_parse_json",
    "_should_retry_with_sanitized_prompt",
    "_validate",
    "sanitize_for_llm_prompt",
]
