"""Business Needs generation (split from Theme Description).

Generates the Jira Business Needs for one selected value stream, organized by the
already-selected Value Stage(s), via an injected LLM. Pure LLM wrapper: no
retrieval, no Azure, no Jira, no stage selection.

Inputs are intentionally narrow: the generated summary / idea context, the
selected value stream, and the selected stages. It does NOT receive historic
examples, historic stage context, or L2/L3 capabilities.

Lenient: a missing llm or malformed output returns an empty string plus warnings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt, safe_json_extract
from vs_app.modules.prompts.schemas import BusinessNeedsResult

_JIRA_ID_RE = re.compile(r"\b(?:IDMT|GROUP|EPIC)-\d+\b", re.IGNORECASE)


def generate_business_needs(
    *,
    idea_context: str,
    value_stream_name: str,
    selected_stages: list[dict[str, Any]] | list[str],
    llm: Any,
) -> dict[str, Any]:
    """Generate business_needs organized by the selected stages.

    Returns ``{business_needs, warnings, raw_response}``.
    """
    warnings: list[str] = []
    if not selected_stages:
        warnings.append("no selected stages provided")

    if llm is None:
        warnings.append("no llm provided for business needs")
        return _result("", warnings, "")

    payload = load_prompt_yaml("business_needs_generation")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        idea_context=_clean(idea_context),
        value_stream_name=_clean(value_stream_name),
        selected_stages=json.dumps(_selected_stage_list(selected_stages), indent=2),
    )
    system_prompt = str(payload.get("system") or "").strip()

    try:
        parsed, raw_response = _call_llm(prompt, system_prompt, llm)
        output = BusinessNeedsResult.model_validate(parsed or {})
    except Exception as exc:  # noqa: BLE001 - lenient: never raise
        warnings.append(f"business needs failed: {type(exc).__name__}")
        return _result("", warnings, "")

    business_needs = _clean(output.business_needs)
    if not business_needs:
        warnings.append("empty business_needs")
    if _JIRA_ID_RE.search(business_needs):
        warnings.append("generated text contains Jira-like IDs")

    return _result(business_needs, warnings, raw_response)


def _call_llm(prompt: str, system_prompt: str, llm: Any) -> tuple[dict[str, Any], str]:
    if hasattr(llm, "generate_structured"):
        result = llm.generate_structured(
            query=prompt,
            output_schema=BusinessNeedsResult,
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


def _result(business_needs: str, warnings: list[str], raw_response: str) -> dict[str, Any]:
    return {
        "business_needs": business_needs,
        "warnings": warnings,
        "raw_response": raw_response,
    }


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = ["generate_business_needs"]
