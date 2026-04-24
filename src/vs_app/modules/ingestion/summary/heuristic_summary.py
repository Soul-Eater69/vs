"""Fallback summary builders used when the LLM path is unavailable."""

from __future__ import annotations

from vs_app.modules.tickets.documents import TicketSummaryDocument


def build_heuristic_summary(
    ticket_key: str,
    ticket_data: dict,
    consolidated_text: str,
) -> TicketSummaryDocument:
    fields = ticket_data.get("fields", {})
    summary_field = str(fields.get("summary") or ticket_key)
    preview = consolidated_text[:400] if consolidated_text else ""
    return TicketSummaryDocument(
        ticket_id=ticket_key,
        summary_text=f"{summary_field}. {preview}".strip(),
        business_problem=preview[:200],
        business_capability="",
        key_terms=[],
    )


__all__ = ["build_heuristic_summary"]
