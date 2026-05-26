from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt, safe_json_extract


class ValueStagePick(BaseModel):
    stage: str = ""
    confidence: float = 0.0
    reason: str = ""


class ValueStageSelectionResult(BaseModel):
    picks: list[ValueStagePick] = Field(default_factory=list)


def predict_value_stream_stages(
    *,
    idea_card_text: str,
    value_stream_name: str,
    allowed_stages: list[str],
    value_stream_description: str = "",
    llm: Any | None = None,
    max_output_stages: int | None = None,
) -> dict[str, Any]:
    allowed = _dedupe_exact([str(stage).strip() for stage in allowed_stages or [] if str(stage).strip()])
    warnings: list[str] = []
    if not allowed:
        return {
            "value_stream_name": value_stream_name,
            "allowed_stages": [],
            "predicted_stages": [],
            "warnings": ["no allowed stages for value stream"],
        }
    if llm is None:
        return {
            "value_stream_name": value_stream_name,
            "allowed_stages": allowed,
            "predicted_stages": [],
            "warnings": ["no llm provided for stage prediction"],
        }

    try:
        parsed = _call_stage_selector_llm(
            idea_card_text=idea_card_text,
            value_stream_name=value_stream_name,
            allowed_stages=allowed,
            value_stream_description=value_stream_description,
            llm=llm,
        )
    except Exception as exc:
        return {
            "value_stream_name": value_stream_name,
            "allowed_stages": allowed,
            "predicted_stages": [],
            "warnings": [f"stage prediction llm failed: {type(exc).__name__}: {exc}"],
        }
    picks = parsed.get("picks") or []

    allowed_set = set(allowed)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        stage = str(pick.get("stage") or "").strip()
        if stage not in allowed_set:
            if stage:
                warnings.append(f"dropped invalid predicted stage: {stage}")
            continue
        if stage in seen:
            continue
        seen.add(stage)
        selected.append(
            {
                "stage": stage,
                "confidence": _clamp_float(pick.get("confidence")),
                "reason": str(pick.get("reason") or "").strip(),
            }
        )
        if max_output_stages is not None and len(selected) >= max_output_stages:
            break

    return {
        "value_stream_name": value_stream_name,
        "allowed_stages": allowed,
        "predicted_stages": selected,
        "warnings": warnings,
    }


def _call_stage_selector_llm(
    *,
    idea_card_text: str,
    value_stream_name: str,
    allowed_stages: list[str],
    value_stream_description: str,
    llm: Any,
) -> dict[str, Any]:
    payload = load_prompt_yaml("value_stage_selection")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        idea_card_text=str(idea_card_text or "").strip(),
        value_stream_name=str(value_stream_name or "").strip(),
        value_stream_description=str(value_stream_description or "").strip(),
        allowed_stages=json.dumps(allowed_stages, indent=2),
    )
    system_prompt = str(payload.get("system") or "").strip()

    if hasattr(llm, "generate_structured"):
        result = llm.generate_structured(
            query=prompt,
            output_schema=ValueStageSelectionResult,
            system_prompt=system_prompt,
            reasoning_effort="low",
        )
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return dict(result or {})

    messages = (
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        if system_prompt
        else [{"role": "user", "content": prompt}]
    )
    if hasattr(llm, "invoke"):
        response = llm.invoke(messages)
    elif hasattr(llm, "generate"):
        response = llm.generate(prompt)
    elif callable(llm):
        response = llm(messages)
    else:
        raise TypeError("llm must provide generate_structured(), invoke(), generate(), or be callable")

    content = getattr(response, "content", response)
    if isinstance(content, dict):
        return content
    return safe_json_extract(str(content or ""))


def _dedupe_exact(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


__all__ = [
    "ValueStagePick",
    "ValueStageSelectionResult",
    "predict_value_stream_stages",
]
