"""Foundational (default) stages for specific Value Streams.

Some Value Streams always include configured foundational stages whenever they are
selected — provided the stage is present in the allowed stage catalog for that
Value Stream. This is deterministic pre-processing, NOT LLM inference: the
foundational map is never passed to the LLM prompt as context.
"""

from __future__ import annotations

from vs_app.stage_generation.models import GeneratedStage
from vs_app.stage_generation.validators import match_allowed_stage

FOUNDATIONAL_STAGE_MAP: dict[str, list[str]] = {
    "Configure, Price, and Quote": [
        "Price Products and Manage Approvals",
        "Generate Quote",
    ],
    "Discover Business Insights": [
        "Explore Information",
    ],
    "Establish Product Offering": [
        "Prepare Product Offering",
    ],
    "Manage Leads and Opportunities": [
        "Perform Outreach",
        "Manage Pipeline & Reporting Analytics",
    ],
    "Order to Cash for Group Coverage": [
        "Account Configuration",
    ],
    "Perform Engagement": [
        "Orchestrate Stakeholder Communication",
    ],
}

# Deterministic attributes for foundational stages.
_FOUNDATIONAL_CONFIDENCE = 1.0  # -> confidence_score 100
_FOUNDATIONAL_RATIONALE = "Foundational stage for selected Value Stream."
_FOUNDATIONAL_SUPPORT = "direct"

# Lookup index keyed by normalized value-stream name.
_NORMALIZED_MAP = {
    " ".join(name.strip().lower().split()): stages
    for name, stages in FOUNDATIONAL_STAGE_MAP.items()
}


def get_foundational_stages(
    value_stream_name: str,
    allowed_stages: list[str],
) -> tuple[list[GeneratedStage], list[str]]:
    """Return (foundational stages, warnings) for a Value Stream.

    Only configured stages that are present in ``allowed_stages`` are included
    (canonicalized to the catalog spelling). A configured stage that is not in the
    allowed catalog is skipped with a warning. Unknown Value Streams return no
    foundational stages.
    """
    configured = _NORMALIZED_MAP.get(" ".join(str(value_stream_name or "").strip().lower().split()))
    if not configured:
        return [], []

    vs_name = " ".join(str(value_stream_name or "").split())
    stages: list[GeneratedStage] = []
    warnings: list[str] = []
    for stage_name in configured:
        canonical = match_allowed_stage(stage_name, allowed_stages)
        if not canonical:
            warnings.append(f"foundational stage not in allowed stages: {stage_name}")
            continue
        stages.append(
            GeneratedStage(
                stage_name=canonical,
                value_stream_name=vs_name,
                rationale=_FOUNDATIONAL_RATIONALE,
                confidence=_FOUNDATIONAL_CONFIDENCE,
                stage_id="",
                support_type=_FOUNDATIONAL_SUPPORT,
            )
        )
    return stages, warnings


__all__ = ["FOUNDATIONAL_STAGE_MAP", "get_foundational_stages"]
