"""Runtime Value Stream generator.

Wraps the existing ``ValueStreamRagService`` and normalizes its rich payload
into the clean :mod:`vs_app.value_stream_generation.models` contract. No new
LLM pass, no Azure/Jira calls of its own — it only adapts the RAG result.

The RAG service already returns slim ``selected_value_streams`` rows plus richer
``candidate_value_streams`` metadata. We join the two by entity id / name so each
generated Value Stream carries support type, evidence, and historic IDMT ids.
"""

from __future__ import annotations

from typing import Any

from vs_app.modules.rag.service import ValueStreamRagCommand, ValueStreamRagService
from vs_app.value_stream_generation.models import (
    GeneratedValueStream,
    ValueStreamGenerationRequest,
    ValueStreamGenerationResult,
)
from vs_app.value_stream_generation.validators import (
    derive_support_type,
    validate_value_stream_name,
)


async def generate_value_streams(
    request: ValueStreamGenerationRequest,
    *,
    service: ValueStreamRagService | None = None,
) -> ValueStreamGenerationResult:
    """Generate runtime Value Stream candidates for a new IDMT request.

    ``service`` is injectable so tests can supply a fake RAG service. By default
    a real ``ValueStreamRagService`` is used (which performs live retrieval when
    actually run).
    """
    warnings: list[str] = []
    if request.custom_instruction:
        warnings.append("custom_instruction provided but not yet applied (deferred)")

    rag_service = service or ValueStreamRagService()
    command = ValueStreamRagCommand(
        ticket_id=request.ticket_id,
        idea_card_text=request.idea_card_text,
        final_output_count=request.top_n,
    )
    result = await rag_service.analyze(command)

    return _result_from_rag(result, request=request, warnings=warnings)


def _result_from_rag(
    result: Any,
    *,
    request: ValueStreamGenerationRequest,
    warnings: list[str],
) -> ValueStreamGenerationResult:
    candidate_index = _index_candidates(
        list(getattr(result, "candidate_value_streams", []) or [])
        + list(getattr(result, "merged_candidate_value_streams", []) or [])
        + list(getattr(result, "llm_candidates", []) or [])
    )

    value_streams: list[GeneratedValueStream] = []
    seen: set[str] = set()
    for row in getattr(result, "selected_value_streams", []) or []:
        if not isinstance(row, dict):
            continue
        raw_name = str(row.get("entity_name") or "").strip()
        canonical = validate_value_stream_name(raw_name)
        if not canonical:
            if raw_name:
                warnings.append(f"dropped non-approved value stream: {raw_name}")
            continue

        key = canonical.lower()
        if key in seen:
            continue
        seen.add(key)

        candidate = _lookup_candidate(candidate_index, row)
        historic_idmt_ids = _first_nonempty_list(
            row.get("supporting_ticket_ids"),
            candidate.get("supporting_ticket_ids"),
        )
        evidence = _first_nonempty_list(
            candidate.get("historical_reasons"),
            candidate.get("historical_evidence"),
        )
        confidence = _float(row.get("confidence"))
        support_type = derive_support_type(
            confidence=confidence,
            historic_idmt_ids=historic_idmt_ids,
            from_semantic=bool(candidate.get("from_semantic")),
            from_historical=bool(candidate.get("from_historical")),
            selection_source=str(row.get("selection_source") or ""),
        )

        value_streams.append(
            GeneratedValueStream(
                name=canonical,
                entity_id=str(row.get("entity_id") or candidate.get("entity_id") or "").strip(),
                support_type=support_type,
                confidence=confidence,
                rationale=str(row.get("reason") or "").strip(),
                evidence=[_text(item) for item in evidence if _text(item)],
                historic_idmt_ids=[_text(item) for item in historic_idmt_ids if _text(item)],
            )
        )

    value_streams = value_streams[: max(1, int(request.top_n or 10))]

    warnings.extend(str(w) for w in getattr(result, "warnings", []) or [])
    debug = {
        "rag_selected_count": len(getattr(result, "selected_value_streams", []) or []),
        "generated_count": len(value_streams),
        "historical_source": str(getattr(result, "historical_source", "") or ""),
    }
    return ValueStreamGenerationResult(
        value_streams=value_streams,
        warnings=_dedupe(warnings),
        debug=debug,
    )


def _index_candidates(candidates: list[dict]) -> dict[str, dict]:
    """Index candidate metadata by id and name keys (first occurrence wins)."""
    index: dict[str, dict] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        id_key = _norm(row.get("entity_id"))
        name_key = _norm(row.get("entity_name"))
        if id_key:
            index.setdefault(f"id:{id_key}", row)
        if name_key:
            index.setdefault(f"name:{name_key}", row)
    return index


def _lookup_candidate(index: dict[str, dict], row: dict) -> dict:
    id_key = _norm(row.get("entity_id"))
    name_key = _norm(row.get("entity_name"))
    return index.get(f"id:{id_key}") or index.get(f"name:{name_key}") or {}


def _first_nonempty_list(*values: Any) -> list:
    for value in values:
        if value:
            return list(value)
    return []


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(value: Any) -> str:
    return _text(value).lower()


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


__all__ = ["generate_value_streams"]
