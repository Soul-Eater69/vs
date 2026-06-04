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
from vs_app.stage_generation.foundational import get_foundational_stages
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

    Stage prediction is summary-only: the only ticket context passed to the
    predictor is ``generated_summary`` (falling back to ``idea_card_text`` for
    legacy callers) — never the raw description, theme text, capabilities, or any
    historic stage context. The summary-only prompt is selected explicitly.
    """
    summary_text = request.generated_summary or (request.idea_card_text or "")
    prediction = predict_fn(
        idea_card_text=summary_text,
        value_stream_name=request.value_stream_name,
        allowed_stages=request.allowed_stages,
        value_stream_description=request.value_stream_description,
        llm=llm,
        max_output_stages=request.max_output_stages,
        prompt_name="value_stage_prediction_summary",
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

    # Foundational (default) stages first — deterministic, no LLM. They are kept on
    # dedup, so an LLM pick of the same stage is dropped in favor of the
    # foundational version.
    foundational_stages, foundational_warnings = get_foundational_stages(
        value_stream_name, request.allowed_stages
    )
    warnings.extend(foundational_warnings)
    for stage in foundational_stages:
        key = stage.stage_name.lower()
        if key in seen:
            continue
        seen.add(key)
        stages.append(stage)

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

        # Map the selector's summary-mode support to support_type when present;
        # blank otherwise. Rejected-stage buckets are never surfaced here.
        support = str(pick.get("support") or "").strip().lower()
        support_type = support if support in {"direct", "implied"} else ""

        stages.append(
            GeneratedStage(
                stage_name=canonical,
                value_stream_name=value_stream_name,
                rationale=str(pick.get("reason") or "").strip(),
                confidence=_float(pick.get("confidence")),
                stage_id="",
                support_type=support_type,
            )
        )

    warnings.extend(str(w) for w in prediction.get("warnings") or [])
    debug = {
        "predicted_count": len(prediction.get("predicted_stages") or []),
        "foundational_count": len(foundational_stages),
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
