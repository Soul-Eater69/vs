"""Prompt builders for ingestion summary workflows."""

from __future__ import annotations

from ingestion.application.prompts import (
    build_value_stream_classification_prompt,
    load_prompt_yaml,
    render_prompt,
)


def build_structured_summary_prompt(*, ticket_id: str, text: str) -> str:
    payload = load_prompt_yaml("retrieval_summary")
    return render_prompt(
        str(payload["template"]),
        ticket_id=ticket_id,
        text=text,
        summary_schema=str(payload["schema"]).strip(),
    )


__all__ = [
    "build_structured_summary_prompt",
    "build_value_stream_classification_prompt",
]
