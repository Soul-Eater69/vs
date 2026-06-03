"""Theme-generation orchestration (Feature 14B2).

Combines, per selected Value Stream:
  allowed stages (catalog) + predict_value_stream_stages (stage picks)
  + Feature 14A retrieval (historic examples) + generate_theme_description (text)
into one Theme result.

Injected clients only: the orchestrator never embeds text, constructs an Azure
client, calls Jira, or wires an API. Retrieval runs only when both an injected
``search_client`` and a caller-supplied ``query_vector`` are provided. Lenient:
LLM/retrieval failures become warnings, never exceptions.
"""

from __future__ import annotations

from typing import Any

from vs_app.theme_generation.descriptions import generate_theme_description
from vs_app.theme_generation.retrieval import (
    extract_matching_theme_refs,
    fetch_theme_examples,
    search_idmt_examples,
    select_theme_examples_for_prompt,
)
from vs_app.modules.stages.stage_catalog import get_allowed_stages
from vs_app.modules.stages.stage_selector import predict_value_stream_stages


def generate_theme_for_value_stream(
    *,
    idea_context: str,
    value_stream_name: str,
    allowed_stages: list[str],
    examples: list[dict[str, Any]],
    llm: Any,
) -> dict[str, Any]:
    """Generate one Theme result for a single selected value stream."""
    warnings: list[str] = []

    stage_result = predict_value_stream_stages(
        idea_card_text=idea_context,
        value_stream_name=value_stream_name,
        allowed_stages=allowed_stages,
        llm=llm,
    )
    warnings.extend(stage_result.get("warnings") or [])

    selected_stages = _validate_selected_stages(
        stage_result.get("predicted_stages") or [], allowed_stages, warnings
    )

    theme_result = generate_theme_description(
        idea_context=idea_context,
        value_stream_name=value_stream_name,
        allowed_stages=allowed_stages,
        selected_stages=selected_stages,
        examples=examples,
        llm=llm,
    )
    warnings.extend(theme_result.get("warnings") or [])

    return {
        "value_stream_name": _clean(value_stream_name),
        "theme_description": _clean(theme_result.get("theme_description")),
        "business_needs": _clean(theme_result.get("business_needs")),
        "selected_stages": selected_stages,
        "examples_used": _examples_used(examples),
        "warnings": _dedupe(warnings),
    }


def generate_themes_for_idea(
    *,
    idea_context: str,
    selected_value_streams: list[str],
    stage_catalog: dict[str, Any],
    llm: Any,
    search_client: Any | None = None,
    query_vector: list[float] | None = None,
    top_k_idmt: int = 15,
    max_examples: int = 5,
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Generate one Theme result per selected value stream, in input order."""
    results: list[dict[str, Any]] = []
    for value_stream_name in selected_value_streams or []:
        vs = _clean(value_stream_name)
        if not vs:
            continue  # blank value stream names are skipped cleanly

        retrieval_warnings: list[str] = []
        allowed_stages = get_allowed_stages(vs, stage_catalog)
        examples = _retrieve_examples(
            search_client=search_client,
            query_vector=query_vector,
            value_stream_name=vs,
            top_k_idmt=top_k_idmt,
            max_examples=max_examples,
            index_name=index_name,
            warnings=retrieval_warnings,
        )

        result = generate_theme_for_value_stream(
            idea_context=idea_context,
            value_stream_name=vs,
            allowed_stages=allowed_stages,
            examples=examples,
            llm=llm,
        )
        result["warnings"] = _dedupe(retrieval_warnings + result["warnings"])
        results.append(result)

    return results


def _retrieve_examples(
    *,
    search_client: Any | None,
    query_vector: list[float] | None,
    value_stream_name: str,
    top_k_idmt: int,
    max_examples: int,
    index_name: str | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    if search_client is None or not query_vector:
        return []
    try:
        idmt_docs = search_idmt_examples(
            search_client=search_client,
            query_vector=query_vector,
            top_k=top_k_idmt,
            index_name=index_name,
        )
        refs = extract_matching_theme_refs(
            idmt_docs=idmt_docs,
            value_stream_name=value_stream_name,
            max_refs=max_examples,
        )
        theme_docs = fetch_theme_examples(
            search_client=search_client, refs=refs, index_name=index_name
        )
        return select_theme_examples_for_prompt(
            theme_docs=theme_docs, max_examples=max_examples
        )
    except Exception as exc:  # noqa: BLE001 - retrieval must not break generation
        warnings.append(f"theme example retrieval failed: {type(exc).__name__}: {exc}")
        return []


def _validate_selected_stages(
    predicted_stages: list[dict[str, Any]],
    allowed_stages: list[str],
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Defensively keep only stages whose name is in the allowed dropdown."""
    allowed = {_norm(stage): _clean(stage) for stage in allowed_stages or [] if _clean(stage)}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pick in predicted_stages or []:
        if not isinstance(pick, dict):
            continue
        name = _clean(pick.get("stage"))
        key = _norm(name)
        if key not in allowed:
            if name:
                warnings.append(f"dropped non-allowed stage: {name}")
            continue
        if key in seen:
            continue
        seen.add(key)
        selected.append(
            {
                "stage": allowed[key],
                "confidence": pick.get("confidence", 0.0),
                "reason": _clean(pick.get("reason")),
            }
        )
    return selected


def _examples_used(examples: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Ids only: ticket_id / group_id / value_stream_name (no text/vectors)."""
    used: list[dict[str, str]] = []
    for example in examples or []:
        if not isinstance(example, dict):
            continue
        value_streams = example.get("value_streams") or []
        vs_name = ""
        if value_streams and isinstance(value_streams[0], dict):
            vs_name = _clean(value_streams[0].get("value_stream_name"))
        used.append(
            {
                "ticket_id": _clean(example.get("ticket_id")),
                "group_id": _clean(example.get("group_id")),
                "value_stream_name": vs_name,
            }
        )
    return used


def _dedupe(warnings: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        text = str(warning)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return _clean(value).lower()


__all__ = [
    "generate_theme_for_value_stream",
    "generate_themes_for_idea",
]
