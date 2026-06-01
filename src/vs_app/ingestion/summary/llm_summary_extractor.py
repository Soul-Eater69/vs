"""LLM-backed summary extraction.

Shared LLM plumbing lives in ``llm_io`` and is re-exported here for backwards
compatibility. Value Stream support classification moved to
``ingestion/ground_truth/value_stream_support`` (Feature 6) and is also
re-exported here so existing imports keep working.
"""

from __future__ import annotations

import logging
from typing import Any

from vs_app.ingestion.ground_truth.value_stream_support import (  # noqa: F401
    ValueStreamClassificationError,
    _match_known_value_stream,
    _normalize_name,
    classify_ticket_value_streams,
)
from vs_app.modules.prompts.schemas import SummaryOutput
from vs_app.modules.tickets.documents import TicketSummaryDocument

from .llm_io import (  # noqa: F401
    _call_llm,
    _input_char_limit,
    _output_token_limit,
    _parse_json,
    _should_retry_with_sanitized_prompt,
    _validate,
    sanitize_for_llm_prompt,
)
from .mapper import parse_structured_summary_payload
from .prompt_builder import build_structured_summary_prompt

logger = logging.getLogger(__name__)


class SummaryExtractionError(RuntimeError):
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


__all__ = [
    "SummaryExtractionError",
    "ValueStreamClassificationError",
    "classify_ticket_value_streams",
    "sanitize_for_llm_prompt",
    "summarize_ticket",
]
