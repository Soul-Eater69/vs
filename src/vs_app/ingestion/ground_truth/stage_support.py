"""Stage support classification (historic ground truth).

Classifies each Jira-verified ground-truth stage as ``direct`` / ``implied`` /
``weak_broad`` / ``not_in_context`` against the original IDMT context. Like
``value_stream_support`` this never invents stages or value streams; it only
classifies the stages supplied in ``gt_by_value_stream``.

It is intentionally lenient: it never raises to block ingestion. If there is no
LLM client, no context, or no GT stages, or if the LLM output is missing/
malformed, it returns ``[]`` and lets the document builder fill the
``unknown`` / ``jira_gt`` fallback for any uncovered GT stage. The classifier
itself never emits ``unknown``.

Shared LLM plumbing is imported from ``ingestion/summary/llm_io``; the prompt and
schema live in ``modules/prompts``.
"""

from __future__ import annotations

import logging
from typing import Any

from vs_app.ingestion.index_documents.models import StageSupport
from vs_app.ingestion.summary.llm_io import (
    _call_llm,
    _input_char_limit,
    _parse_json,
    _should_retry_with_sanitized_prompt,
    _validate,
    sanitize_for_llm_prompt,
)
from vs_app.modules.prompts.loader import build_stage_support_classification_prompt
from vs_app.modules.prompts.schemas import StageSupportResult

logger = logging.getLogger(__name__)

_VALID_SUPPORT_TYPES = ("direct", "implied", "weak_broad", "not_in_context")
_SOURCE = "llm_stage_support"


class StageSupportClassificationError(RuntimeError):
    pass


def classify_stage_support(
    *,
    ticket_id: str,
    consolidated_text: str,
    gt_by_value_stream: dict[str, list[str]],
    llm_client: Any,
    cfg: Any,
) -> list[StageSupport]:
    """Classify the supplied GT stages against the original ticket context.

    Returns one ``StageSupport`` row per classified (value stream, stage). Rows
    for invented value streams/stages or invalid support types are dropped, and
    stages the LLM omits are simply not returned (the document builder backfills
    them as ``unknown``). Never raises; returns ``[]`` on any failure.
    """
    allowed = _build_allowed(gt_by_value_stream)
    if not allowed or llm_client is None or not consolidated_text.strip():
        return []

    text = consolidated_text[:_input_char_limit(cfg, "classification_input_char_limit")]
    try:
        output = _classify_once(ticket_id, text, allowed, llm_client, cfg)
    except StageSupportClassificationError as exc:
        if not _should_retry_with_sanitized_prompt(exc, cfg):
            logger.warning("Stage support classification failed for %s: %s", ticket_id, exc)
            return []
        sanitized = sanitize_for_llm_prompt(consolidated_text)[
            : _input_char_limit(cfg, "classification_input_char_limit")
        ]
        if sanitized == text:
            return []
        logger.warning(
            "Retrying %s stage support classification with sanitized prompt after content-filter response",
            ticket_id,
        )
        try:
            output = _classify_once(ticket_id, sanitized, allowed, llm_client, cfg)
        except Exception as retry_exc:  # noqa: BLE001 - non-blocking ingestion support
            logger.warning("Stage support retry failed for %s: %s", ticket_id, retry_exc)
            return []
    except Exception as exc:  # noqa: BLE001 - non-blocking ingestion support
        # Any unexpected failure (parser changes, gateway errors) must not block
        # ingestion; uncovered GT stages fall through to the builder's unknown row.
        logger.warning("Stage support classification failed for %s: %s", ticket_id, exc)
        return []

    return _build_rows(output, allowed)


def _classify_once(
    ticket_id: str,
    text: str,
    allowed: dict[str, dict[str, Any]],
    llm_client: Any,
    cfg: Any,
) -> StageSupportResult:
    prompt = build_stage_support_classification_prompt(
        ticket_id=ticket_id,
        text=text,
        stages=_format_gt_stage_block(allowed),
    )
    raw = _call_llm(prompt, llm_client, cfg, error_cls=StageSupportClassificationError)
    parsed = _parse_json(raw, f"{ticket_id}:stage_support")
    return _validate(
        StageSupportResult,
        parsed,
        f"{ticket_id}:stage_support",
        StageSupportClassificationError,
    )


def _build_allowed(gt_by_value_stream: dict[str, list[str]] | None) -> dict[str, dict[str, Any]]:
    """Build a lookup of the supplied GT, keyed by normalized names.

    Shape: ``{vs_norm: {"name": vs_name, "stages": {stage_norm: stage_name}}}``.
    """
    allowed: dict[str, dict[str, Any]] = {}
    for value_stream_name, stages in (gt_by_value_stream or {}).items():
        vs_clean = _clean(value_stream_name)
        if not vs_clean:
            continue
        stage_map: dict[str, str] = {}
        for stage in stages or []:
            stage_clean = _clean(stage)
            if stage_clean:
                stage_map.setdefault(stage_clean.lower(), stage_clean)
        if stage_map:
            allowed.setdefault(vs_clean.lower(), {"name": vs_clean, "stages": stage_map})
    return allowed


def _format_gt_stage_block(allowed: dict[str, dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in allowed.values():
        lines.append(f"Value Stream: {entry['name']}")
        for stage_name in entry["stages"].values():
            lines.append(f"  - {stage_name}")
    return "\n".join(lines)


def _build_rows(
    output: StageSupportResult,
    allowed: dict[str, dict[str, Any]],
) -> list[StageSupport]:
    rows: list[StageSupport] = []
    seen: set[tuple[str, str]] = set()
    for item in output.stages:
        entry = allowed.get(_clean(item.value_stream_name).lower())
        if not entry:
            continue  # invented / unknown value stream
        stage_name = entry["stages"].get(_clean(item.stage_name).lower())
        if not stage_name:
            continue  # invented / unknown stage
        support_type = _clean(item.support_type).lower()
        if support_type not in _VALID_SUPPORT_TYPES:
            continue  # invalid / "unknown" label -> let builder backfill
        key = (entry["name"], stage_name)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            StageSupport(
                value_stream_name=entry["name"],
                stage_name=stage_name,
                support_type=support_type,
                reason=_clean(item.reason),
                evidence=_clean(item.evidence),
                source=_SOURCE,
                confidence=_clamp_confidence(item.confidence),
            )
        )
    return rows


def _clamp_confidence(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = ["StageSupportClassificationError", "classify_stage_support"]
