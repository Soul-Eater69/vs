from __future__ import annotations

import difflib
import json
import re
from typing import Any

from vs_app.modules.prompts.loader import load_prompt_yaml, render_prompt, safe_json_extract


def canonicalize_stage(
    raw_stage: str,
    allowed_stages: list[str],
    *,
    llm: Any | None = None,
    fuzzy_threshold: float = 0.86,
    llm_threshold: float = 0.70,
    value_stream_name: str = "",
) -> dict[str, Any]:
    raw = _clean_stage(raw_stage)
    options = _stage_options(allowed_stages)
    warnings: list[str] = []

    if not raw:
        return _unresolved(raw_stage, "empty raw stage")
    if not options:
        return _unresolved(raw, "no allowed stages")

    raw_norm = normalize_stage_text(raw)

    for option in options:
        if raw == option["name"]:
            return _matched(raw, option["name"], "exact", 1.0)

    for option in options:
        if raw_norm == option["norm"]:
            return _matched(raw, option["name"], "normalized", 1.0)

    for option in options:
        if raw_norm in option["alias_norms"]:
            return _matched(raw, option["name"], "alias", 1.0)

    scored = sorted(
        (
            (_stage_similarity(raw_norm, option["norm"]), option)
            for option in options
            if option["norm"]
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    top_score, top_option = scored[0] if scored else (0.0, None)
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    ambiguous = bool(second_score and top_score - second_score < 0.03 and top_score < 0.97)

    if top_option and top_score >= fuzzy_threshold and not ambiguous:
        return _matched(raw, top_option["name"], "fuzzy", top_score)

    if ambiguous:
        warnings.append(
            f"ambiguous fuzzy match: {top_option['name']}={top_score:.2f}, "
            f"{scored[1][1]['name']}={second_score:.2f}"
        )

    if llm is not None:
        try:
            llm_result = _canonicalize_with_llm(
                raw_stage=raw,
                value_stream_name=value_stream_name,
                allowed_stage_names=[option["name"] for option in options],
                llm=llm,
            )
        except Exception as exc:
            llm_result = {}
            warnings.append(f"llm canonicalization failed: {type(exc).__name__}: {exc}")
        canonical = str(llm_result.get("canonical_stage") or "").strip()
        confidence = _clamp_float(llm_result.get("confidence"))
        allowed_by_name = {option["name"]: option["name"] for option in options}
        if canonical in allowed_by_name and confidence >= llm_threshold:
            result = _matched(raw, allowed_by_name[canonical], "llm", confidence)
            reason = str(llm_result.get("reason") or "").strip()
            if reason:
                result["warnings"].append(reason)
            return result
        warnings.append("llm did not return a confident allowed stage")

    result = _unresolved(raw, "no confident approved stage match")
    result["confidence"] = top_score
    result["warnings"].extend(warnings)
    return result


def normalize_stage_text(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\band\b", " ", text)
    return " ".join(text.split())


def _stage_options(allowed_stages: list[Any]) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in allowed_stages or []:
        if isinstance(item, dict):
            name = _clean_stage(
                item.get("name")
                or item.get("stage_name")
                or item.get("value_stream_stage_name")
            )
            aliases = item.get("aliases") or item.get("stage_aliases") or []
        else:
            name = _clean_stage(item)
            aliases = []
        norm = normalize_stage_text(name)
        if not name or norm in seen:
            continue
        seen.add(norm)
        alias_norms = {
            normalize_stage_text(alias)
            for alias in (aliases if isinstance(aliases, list) else [aliases])
            if normalize_stage_text(alias)
        }
        options.append({"name": name, "norm": norm, "alias_norms": alias_norms})
    return options


def _stage_similarity(raw_norm: str, allowed_norm: str) -> float:
    if not raw_norm or not allowed_norm:
        return 0.0
    if raw_norm == allowed_norm:
        return 1.0

    ratio = difflib.SequenceMatcher(None, raw_norm, allowed_norm).ratio()
    raw_tokens = raw_norm.split()
    allowed_tokens = allowed_norm.split()

    if raw_norm.startswith(allowed_norm) or allowed_norm.startswith(raw_norm):
        ratio = max(ratio, 0.92)
    elif set(allowed_tokens).issubset(set(raw_tokens)):
        extra_tokens = max(0, len(raw_tokens) - len(allowed_tokens))
        ratio = max(ratio, max(0.86, 0.92 - extra_tokens * 0.01))
    elif _is_ordered_subsequence(allowed_tokens, raw_tokens):
        ratio = max(ratio, 0.88)

    return min(1.0, ratio)


def _is_ordered_subsequence(needles: list[str], haystack: list[str]) -> bool:
    if not needles:
        return False
    pos = 0
    for token in haystack:
        if pos < len(needles) and token == needles[pos]:
            pos += 1
    return pos == len(needles)


def _canonicalize_with_llm(
    *,
    raw_stage: str,
    value_stream_name: str,
    allowed_stage_names: list[str],
    llm: Any,
) -> dict[str, Any]:
    payload = load_prompt_yaml("value_stage_canonicalization")
    prompt = render_prompt(
        str(payload.get("user") or payload.get("template") or ""),
        raw_stage=raw_stage,
        value_stream_name=value_stream_name,
        allowed_stages=json.dumps(allowed_stage_names, indent=2),
    )
    system = str(payload.get("system") or "").strip()
    messages = (
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        if system
        else [{"role": "user", "content": prompt}]
    )

    if hasattr(llm, "invoke"):
        response = llm.invoke(messages)
    elif hasattr(llm, "generate"):
        response = llm.generate(prompt)
    elif callable(llm):
        response = llm(messages)
    else:
        raise TypeError("llm must provide invoke(), generate(), or be callable")

    content = getattr(response, "content", response)
    if isinstance(content, dict):
        return content
    return safe_json_extract(str(content or ""))


def _matched(raw: str, canonical: str, method: str, confidence: float) -> dict[str, Any]:
    return {
        "raw": raw,
        "canonical": canonical,
        "match_method": method,
        "confidence": round(_clamp_float(confidence), 4),
        "warnings": [],
    }


def _unresolved(raw: Any, reason: str) -> dict[str, Any]:
    return {
        "raw": _clean_stage(raw),
        "canonical": None,
        "match_method": "unresolved",
        "confidence": 0.0,
        "warnings": [reason],
    }


def _clean_stage(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("\u00a0", " ")
    return " ".join(text.strip(" -*:\t\r\n").split())


def _clamp_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.0
    return max(0.0, min(1.0, parsed))


__all__ = [
    "canonicalize_stage",
    "normalize_stage_text",
]
