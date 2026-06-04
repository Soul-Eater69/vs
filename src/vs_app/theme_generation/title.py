"""Theme-title generation.

Pure LLM wrapper over an injected ``llm`` that produces a single concise Jira
Theme title from the full theme context (Value Stream, current selected stages,
description, business needs, L2/L3 capabilities). No retrieval, no Azure, no
Jira, and no historic stage context.

Lenient: a missing llm or malformed output returns an empty title plus warnings,
never an exception.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt, safe_json_extract
from vs_app.modules.prompts.schemas.title import ThemeTitleResult
from vs_app.theme_generation.models import GeneratedL2Capability, GeneratedL3Capability

# Jira id shapes that must not appear in a generated title.
_JIRA_ID_RE = re.compile(r"\b(?:IDMT|GROUP|EPIC)-\d+\b", re.IGNORECASE)


def generate_theme_title(
    *,
    idea_context: str,
    value_stream_name: str,
    selected_stages: list[Any],
    theme_description: str,
    business_needs: str,
    l2_capabilities: list[GeneratedL2Capability] | None = None,
    l3_capabilities: list[GeneratedL3Capability] | None = None,
    llm: Any | None = None,
) -> tuple[str, list[str]]:
    """Generate one theme title. Returns (title, warnings)."""
    warnings: list[str] = []
    if llm is None:
        return "", ["no llm provided for title generation"]

    payload = load_prompt_yaml("theme_title_generation")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        idea_context=_clean(idea_context),
        value_stream_name=_clean(value_stream_name),
        selected_stages=json.dumps(_stage_names(selected_stages), indent=2),
        theme_description=_clean(theme_description),
        business_needs=_clean(business_needs),
        l2_capabilities=json.dumps(_l2_names(l2_capabilities), indent=2),
        l3_capabilities=json.dumps(_l3_names(l3_capabilities), indent=2),
    )
    system_prompt = str(payload.get("system") or "").strip()

    try:
        parsed = _call_structured(prompt, system_prompt, llm)
        result = ThemeTitleResult.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 - lenient: never raise
        warnings.append(f"title generation failed: {type(exc).__name__}")
        return "", warnings

    title = _clean(result.theme_title)
    if not title:
        warnings.append("empty theme_title")
    elif _JIRA_ID_RE.search(title):
        warnings.append("generated title contains Jira-like IDs")
    return title, warnings


def _call_structured(prompt: str, system_prompt: str, llm: Any) -> dict[str, Any]:
    if hasattr(llm, "generate_structured"):
        result = llm.generate_structured(
            query=prompt,
            output_schema=ThemeTitleResult,
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


def _l2_names(l2_capabilities: list[GeneratedL2Capability] | None) -> list[str]:
    return [_clean(cap.capability_name) for cap in l2_capabilities or [] if _clean(cap.capability_name)]


def _l3_names(l3_capabilities: list[GeneratedL3Capability] | None) -> list[str]:
    names: list[str] = []
    for cap in l3_capabilities or []:
        name = _clean(cap.capability_name)
        if not name:
            continue
        parent = _clean(cap.parent_l2_capability_name)
        names.append(f"{name} (under {parent})" if parent else name)
    return names


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = ["generate_theme_title"]
