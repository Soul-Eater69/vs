"""Pure theme text generation (Feature 14B1).

Generates ``theme_description`` + ``business_needs`` for one selected value
stream via an injected LLM. Pure LLM wrapper: no retrieval, no embedding, no
Azure, no Jira, and no stage prediction. Stages are supplied by the caller (from
``predict_value_stream_stages`` in Feature 14B2) and are only context here — this
function never selects or invents stages.

The ``llm`` duck-type matches ``predict_value_stream_stages``: an object exposing
``generate_structured(query, output_schema, system_prompt, reasoning_effort)``,
or ``invoke``/``generate``, or a callable. Lenient: never raises — failures
become blank fields plus warnings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vs_app.modules.prompts.loader import (
    build_theme_generation_prompt,
    build_theme_generation_system_prompt,
    safe_json_extract,
)
from vs_app.modules.prompts.schemas import ThemeGenerationResult

# Jira id shapes that must not appear in generated narrative text.
_JIRA_ID_RE = re.compile(r"\b(?:IDMT|GROUP|EPIC)-\d+\b", re.IGNORECASE)

_EXAMPLE_FIELDS = ("ticket_id", "group_id", "theme_description", "business_needs", "value_streams", "stages")


def generate_theme_description(
    *,
    idea_context: str,
    value_stream_name: str,
    allowed_stages: list[str],
    selected_stages: list[dict[str, Any]] | list[str],
    examples: list[dict[str, Any]],
    llm: Any,
) -> dict[str, Any]:
    """Generate theme_description + business_needs for the selected value stream."""
    warnings: list[str] = []
    if not allowed_stages:
        warnings.append("no allowed stages for value stream")
    if not selected_stages:
        warnings.append("no selected stages provided")
    if not examples:
        warnings.append("no historic examples available")

    if llm is None:
        warnings.append("no llm provided for theme generation")
        return _result("", "", warnings, "")

    prompt = build_theme_generation_prompt(
        idea_context=_clean(idea_context),
        value_stream_name=_clean(value_stream_name),
        allowed_stages=json.dumps(_stage_name_list(allowed_stages), indent=2),
        selected_stages=json.dumps(_selected_stage_list(selected_stages), indent=2),
        examples=json.dumps(_compact_examples(examples), indent=2),
    )
    system_prompt = build_theme_generation_system_prompt()

    try:
        parsed, raw_response = _call_llm(prompt, system_prompt, llm)
    except Exception as exc:  # noqa: BLE001 - lenient: never raise
        warnings.append(f"theme generation llm failed: {type(exc).__name__}: {exc}")
        return _result("", "", warnings, "")

    try:
        output = ThemeGenerationResult.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 - tolerate malformed payloads
        warnings.append(f"theme generation output invalid: {type(exc).__name__}")
        return _result("", "", warnings, raw_response)

    theme_description = _clean(output.theme_description)
    business_needs = _clean(output.business_needs)

    if not theme_description:
        warnings.append("empty theme_description")
    if _JIRA_ID_RE.search(theme_description) or _JIRA_ID_RE.search(business_needs):
        warnings.append("generated text contains Jira-like IDs")

    return _result(theme_description, business_needs, warnings, raw_response)


def _call_llm(prompt: str, system_prompt: str, llm: Any) -> tuple[dict[str, Any], str]:
    if hasattr(llm, "generate_structured"):
        result = llm.generate_structured(
            query=prompt,
            output_schema=ThemeGenerationResult,
            system_prompt=system_prompt,
            reasoning_effort="low",
        )
        if hasattr(result, "model_dump"):
            parsed = result.model_dump()
            raw = (
                result.model_dump_json()
                if hasattr(result, "model_dump_json")
                else json.dumps(parsed, ensure_ascii=False)
            )
            return parsed, raw
        parsed = dict(result or {})
        return parsed, json.dumps(parsed, ensure_ascii=False)

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
        return content, json.dumps(content, ensure_ascii=False)
    raw_response = str(content or "")
    return safe_json_extract(raw_response), raw_response


def _compact_examples(examples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only safe, compact example fields (no content_vector / raw content)."""
    compact: list[dict[str, Any]] = []
    for example in examples or []:
        if not isinstance(example, dict):
            continue
        compact.append({field: example.get(field) for field in _EXAMPLE_FIELDS if field in example})
    return compact


def _selected_stage_list(selected_stages: list[dict[str, Any]] | list[str]) -> list[str]:
    names: list[str] = []
    for stage in selected_stages or []:
        if isinstance(stage, dict):
            name = _clean(stage.get("stage") or stage.get("stage_name"))
        else:
            name = _clean(stage)
        if name:
            names.append(name)
    return names


def _stage_name_list(allowed_stages: list[str]) -> list[str]:
    return [_clean(stage) for stage in allowed_stages or [] if _clean(stage)]


def _result(theme_description: str, business_needs: str, warnings: list[str], raw_response: str) -> dict[str, Any]:
    return {
        "theme_description": theme_description,
        "business_needs": business_needs,
        "warnings": warnings,
        "raw_response": raw_response,
    }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = ["generate_theme_description"]
