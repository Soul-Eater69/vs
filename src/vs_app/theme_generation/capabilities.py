"""L2 / L3 capability generation for a theme.

Pure LLM wrappers over an injected ``llm``. L2 is generated first; L3 is
generated after, taking the generated L2 capabilities as parent context. No
retrieval, no Azure, no Jira, and no historic stage context — example inputs are
compacted to theme description / business-needs only.

Lenient: a missing llm or malformed output returns an empty list plus warnings,
never an exception.
"""

from __future__ import annotations

import json
from typing import Any

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt, safe_json_extract
from vs_app.modules.prompts.schemas.capability import L2CapabilityResult, L3CapabilityResult
from vs_app.theme_generation.models import GeneratedL2Capability, GeneratedL3Capability

# Example fields that may be shown to the LLM — never includes stages.
_EXAMPLE_FIELDS = ("value_stream_name", "theme_description", "business_needs")


def generate_l2_capabilities(
    *,
    idea_context: str,
    value_stream_name: str,
    selected_stages: list[Any],
    theme_description: str,
    business_needs: str,
    examples: list[dict[str, Any]] | None = None,
    llm: Any | None = None,
) -> tuple[list[GeneratedL2Capability], list[str]]:
    """Generate L2 capabilities for one theme. Returns (capabilities, warnings)."""
    warnings: list[str] = []
    if llm is None:
        return [], ["no llm provided for l2 capability generation"]

    payload = load_prompt_yaml("capability_l2_generation")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        idea_context=_clean(idea_context),
        value_stream_name=_clean(value_stream_name),
        selected_stages=json.dumps(_stage_names(selected_stages), indent=2),
        theme_description=_clean(theme_description),
        business_needs=_clean(business_needs),
        examples=json.dumps(_compact_examples(examples), indent=2),
    )
    system_prompt = str(payload.get("system") or "").strip()

    try:
        parsed = _call_structured(prompt, system_prompt, L2CapabilityResult, llm)
        result = L2CapabilityResult.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 - lenient: never raise
        warnings.append(f"l2 capability generation failed: {type(exc).__name__}")
        return [], warnings

    capabilities: list[GeneratedL2Capability] = []
    seen: set[str] = set()
    for item in result.capabilities:
        name = _clean(item.capability_name)
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        capabilities.append(
            GeneratedL2Capability(
                capability_name=name,
                rationale=_clean(item.rationale),
                confidence=_clamp01(item.confidence),
            )
        )
    if not capabilities:
        warnings.append("no l2 capabilities generated")
    return capabilities, warnings


def generate_l3_capabilities(
    *,
    idea_context: str,
    value_stream_name: str,
    selected_stages: list[Any],
    theme_description: str,
    business_needs: str,
    l2_capabilities: list[GeneratedL2Capability],
    llm: Any | None = None,
) -> tuple[list[GeneratedL3Capability], list[str]]:
    """Generate L3 capabilities after L2. Returns (capabilities, warnings)."""
    warnings: list[str] = []
    if llm is None:
        return [], ["no llm provided for l3 capability generation"]
    if not l2_capabilities:
        return [], ["no l2 capabilities available for l3 generation"]

    allowed_parents = {cap.capability_name.lower(): cap.capability_name for cap in l2_capabilities}

    payload = load_prompt_yaml("capability_l3_generation")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        idea_context=_clean(idea_context),
        value_stream_name=_clean(value_stream_name),
        selected_stages=json.dumps(_stage_names(selected_stages), indent=2),
        theme_description=_clean(theme_description),
        business_needs=_clean(business_needs),
        l2_capabilities=json.dumps([cap.capability_name for cap in l2_capabilities], indent=2),
    )
    system_prompt = str(payload.get("system") or "").strip()

    try:
        parsed = _call_structured(prompt, system_prompt, L3CapabilityResult, llm)
        result = L3CapabilityResult.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 - lenient: never raise
        warnings.append(f"l3 capability generation failed: {type(exc).__name__}")
        return [], warnings

    capabilities: list[GeneratedL3Capability] = []
    seen: set[tuple[str, str]] = set()
    for item in result.capabilities:
        name = _clean(item.capability_name)
        parent_raw = _clean(item.parent_l2_capability_name)
        parent = allowed_parents.get(parent_raw.lower())
        if not name:
            continue
        if not parent:
            if parent_raw:
                warnings.append(f"dropped l3 with unknown parent L2: {parent_raw}")
            continue
        key = (name.lower(), parent.lower())
        if key in seen:
            continue
        seen.add(key)
        capabilities.append(
            GeneratedL3Capability(
                capability_name=name,
                parent_l2_capability_name=parent,
                rationale=_clean(item.rationale),
                confidence=_clamp01(item.confidence),
            )
        )
    if not capabilities:
        warnings.append("no l3 capabilities generated")
    return capabilities, warnings


def _call_structured(prompt: str, system_prompt: str, output_schema: Any, llm: Any) -> dict[str, Any]:
    if hasattr(llm, "generate_structured"):
        result = llm.generate_structured(
            query=prompt,
            output_schema=output_schema,
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


def _stage_names(selected_stages: list[Any]) -> list[str]:
    names: list[str] = []
    for stage in selected_stages or []:
        if isinstance(stage, dict):
            name = _clean(stage.get("stage") or stage.get("stage_name"))
        else:
            name = _clean(getattr(stage, "stage_name", stage))
        if name:
            names.append(name)
    return names


def _compact_examples(examples: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Keep only safe example fields — never stages, vectors, or raw content."""
    compact: list[dict[str, Any]] = []
    for example in examples or []:
        if not isinstance(example, dict):
            continue
        compact.append({field: example.get(field) for field in _EXAMPLE_FIELDS if field in example})
    return compact


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = ["generate_l2_capabilities", "generate_l3_capabilities"]
