"""Runtime stage generator.

Wraps the existing stage predictor (``predict_value_stream_stages``) and
normalizes its payload into the clean :mod:`vs_app.stage_generation.models`
contract. No new LLM pass and no historic stage context — it only adapts the
predictor's output and validates every stage against the allowed dropdown.

Synchronous, matching the existing stage selector (no async).
"""

from __future__ import annotations

from typing import Any, Callable

from vs_app.modules.stages.stage_selector import predict_value_stream_stages
from vs_app.stage_generation.models import (
    GeneratedStage,
    StageGenerationRequest,
    StageGenerationResult,
)
from vs_app.stage_generation.validators import match_allowed_stage


def generate_stages(
    request: StageGenerationRequest,
    *,
    llm: Any | None = None,
    predict_fn: Callable[..., dict] = predict_value_stream_stages,
) -> StageGenerationResult:
    """Generate runtime stages for one Value Stream.

    ``llm`` and ``predict_fn`` are injectable so tests can supply fakes; the
    default predictor performs a live LLM call only when actually run with an
    ``llm``.
    """
    prediction = predict_fn(
        idea_card_text=request.idea_card_text or "",
        value_stream_name=request.value_stream_name,
        allowed_stages=request.allowed_stages,
        value_stream_description=request.value_stream_description,
        llm=llm,
        max_output_stages=request.max_output_stages,
    )
    return _result_from_prediction(prediction, request=request)


def _result_from_prediction(
    prediction: dict,
    *,
    request: StageGenerationRequest,
) -> StageGenerationResult:
    value_stream_name = str(
        prediction.get("value_stream_name") or request.value_stream_name or ""
    ).strip()
    warnings: list[str] = []

    stages: list[GeneratedStage] = []
    seen: set[str] = set()
    for pick in prediction.get("predicted_stages") or []:
        if not isinstance(pick, dict):
            continue
        raw_stage = str(pick.get("stage") or "").strip()
        canonical = match_allowed_stage(raw_stage, request.allowed_stages)
        if not canonical:
            if raw_stage:
                warnings.append(f"dropped invented stage: {raw_stage}")
            continue

        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)

        stages.append(
            GeneratedStage(
                stage_name=canonical,
                value_stream_name=value_stream_name,
                rationale=str(pick.get("reason") or "").strip(),
                confidence=_float(pick.get("confidence")),
                stage_id="",
                support_type="",
            )
        )

    warnings.extend(str(w) for w in prediction.get("warnings") or [])
    debug = {
        "predicted_count": len(prediction.get("predicted_stages") or []),
        "generated_count": len(stages),
    }
    return StageGenerationResult(
        value_stream_name=value_stream_name,
        stages=stages,
        warnings=_dedupe(warnings),
        debug=debug,
    )


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


__all__ = ["generate_stages"]
