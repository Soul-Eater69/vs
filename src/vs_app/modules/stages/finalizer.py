from __future__ import annotations

import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt

logger = logging.getLogger(__name__)

StageScope = Literal["specific_stages", "entire_value_stream", "broad_or_unclear"]


class StagePick(BaseModel):
    stage_id: str = ""
    stage_sequence: int | None = None
    stage_name: str = ""
    confidence: float = 0.5
    reason: str = ""


class StageSelectionResult(BaseModel):
    value_stream_id: str = ""
    value_stream_name: str = ""
    stage_scope: StageScope = "broad_or_unclear"
    selected_stages: list[StagePick] = Field(default_factory=list)
    reason: str = ""


def select_stages_with_llm(
    *,
    condensed_idea_card: str,
    selected_value_stream: dict,
    candidate_stages: list[dict],
    generation_service: Any | None = None,
) -> dict:
    value_stream_id = str(selected_value_stream.get("entity_id") or "").strip()
    value_stream_name = str(selected_value_stream.get("entity_name") or "").strip()
    candidates = list(candidate_stages or [])
    if not candidates:
        return _empty_prediction(
            value_stream_id,
            value_stream_name,
            "No stages found for selected value stream.",
        )

    parsed = _call_stage_llm(
        condensed_idea_card=condensed_idea_card,
        selected_value_stream=selected_value_stream,
        candidate_stages=candidates,
        generation_service=generation_service,
    )
    return _validate_stage_selection(
        parsed,
        selected_value_stream=selected_value_stream,
        candidate_stages=candidates,
    )


def _call_stage_llm(
    *,
    condensed_idea_card: str,
    selected_value_stream: dict,
    candidate_stages: list[dict],
    generation_service: Any | None,
) -> dict:
    value_stream_id = str(selected_value_stream.get("entity_id") or "").strip()
    value_stream_name = str(selected_value_stream.get("entity_name") or "").strip()
    payload = load_prompt_yaml("stage_selection")
    prompt = render_prompt(
        str(payload["user"]),
        condensed_idea_card=str(condensed_idea_card or "").strip(),
        value_stream_id=value_stream_id,
        value_stream_name=value_stream_name,
        value_stream_description=str(selected_value_stream.get("reason") or ""),
        candidate_stages=_format_candidate_stages(candidate_stages),
    )
    system_prompt = str(payload.get("system") or "").strip()
    effort = os.environ.get("STAGE_SELECTION_REASONING_EFFORT", "low")

    try:
        if generation_service is None:
            from vs_app.integrations.clients.generation_service import GenerationService

            service = GenerationService()
        else:
            service = generation_service
        result = service.generate_structured(
            query=prompt,
            output_schema=StageSelectionResult,
            system_prompt=system_prompt,
            reasoning_effort=effort,
        )
        return result.model_dump() if hasattr(result, "model_dump") else dict(result or {})
    except Exception as exc:
        logger.exception("[STAGE] stage selection LLM failed")
        return {
            "value_stream_id": value_stream_id,
            "value_stream_name": value_stream_name,
            "stage_scope": "broad_or_unclear",
            "selected_stages": [],
            "reason": f"Stage selection LLM failed: {type(exc).__name__}",
        }


def _validate_stage_selection(
    parsed: dict,
    *,
    selected_value_stream: dict,
    candidate_stages: list[dict],
) -> dict:
    value_stream_id = str(selected_value_stream.get("entity_id") or "").strip()
    value_stream_name = str(selected_value_stream.get("entity_name") or "").strip()
    candidate_by_id = {
        str(stage.get("stage_id") or "").strip(): stage
        for stage in candidate_stages
        if str(stage.get("stage_id") or "").strip()
    }
    scope = str(parsed.get("stage_scope") or "broad_or_unclear").strip()
    if scope not in {"specific_stages", "entire_value_stream", "broad_or_unclear"}:
        scope = "broad_or_unclear"

    selected: list[dict] = []
    seen: set[str] = set()
    for pick in parsed.get("selected_stages") or []:
        pick_id = str(pick.get("stage_id") if isinstance(pick, dict) else "").strip()
        if not pick_id or pick_id in seen or pick_id not in candidate_by_id:
            continue
        seen.add(pick_id)
        candidate = candidate_by_id[pick_id]
        selected.append(
            {
                "stage_id": str(candidate.get("stage_id") or "").strip(),
                "stage_sequence": candidate.get("stage_sequence"),
                "stage_name": str(candidate.get("stage_name") or "").strip(),
                "confidence": _clamp_float(pick.get("confidence") if isinstance(pick, dict) else 0.5),
                "reason": str((pick.get("reason") if isinstance(pick, dict) else "") or "").strip(),
            }
        )
        if len(selected) >= 3:
            break

    if selected:
        scope = "specific_stages"
    elif scope == "specific_stages":
        scope = "broad_or_unclear"

    return {
        "value_stream_id": value_stream_id,
        "value_stream_name": value_stream_name,
        "stage_scope": scope,
        "selected_stages": selected,
        "reason": str(parsed.get("reason") or "").strip(),
    }


def _format_candidate_stages(candidate_stages: list[dict]) -> str:
    blocks: list[str] = []
    for stage in candidate_stages:
        lines = [
            f"Stage ID: {stage.get('stage_id', '')}",
            f"Sequence: {stage.get('stage_sequence', '')}",
            f"Name: {stage.get('stage_name', '')}",
            f"Description: {stage.get('stage_description', '')}",
            f"Entrance Criteria: {stage.get('stage_entrance_criteria', '')}",
            f"Exit Criteria: {stage.get('stage_exit_criteria', '')}",
            f"Value Items: {stage.get('stage_value_items', '')}",
            f"Stakeholders: {stage.get('stage_stakeholders', '')}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _empty_prediction(value_stream_id: str, value_stream_name: str, reason: str) -> dict:
    return {
        "value_stream_id": value_stream_id,
        "value_stream_name": value_stream_name,
        "stage_scope": "broad_or_unclear",
        "selected_stages": [],
        "reason": reason,
    }


def _clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.5
    return max(0.0, min(1.0, parsed))


__all__ = [
    "StagePick",
    "StageSelectionResult",
    "select_stages_with_llm",
]
