"""Validation and deterministic derivation for runtime Value Stream generation.

Two responsibilities:
- Validate generated names against the approved Value Stream set (reuse the
  canonical registry; drop anything that is not approved).
- Derive ``support_type`` (direct / implied) from existing RAG candidate
  metadata, deterministically and without any extra LLM call.
"""

from __future__ import annotations

from vs_app.modules.value_streams.canonical import canonicalize_value_stream_name

# A candidate is "direct" when its confidence clears this bar, even without
# explicit historical support.
_DIRECT_CONFIDENCE = 0.7


def validate_value_stream_name(raw_name: str) -> str | None:
    """Return the canonical approved Value Stream name, or ``None`` if not approved.

    Thin wrapper over the canonical registry so the generator has a single,
    obvious validation entry point.
    """
    return canonicalize_value_stream_name(raw_name)


def derive_support_type(
    *,
    confidence: float,
    historic_idmt_ids: list[str],
    from_semantic: bool,
    from_historical: bool,
    selection_source: str,
) -> str:
    """Deterministically classify a selected Value Stream as ``direct`` or ``implied``.

    Direct when the candidate has real support behind it:
    - historical support from retrieved IDMTs (``from_historical`` with concrete
      ``historic_idmt_ids``), or
    - both semantic and historical signals, or
    - a strong confidence score.

    Otherwise implied (adjacent / weaker / backfilled). Safe-backfill picks are
    always implied: they are explicitly low-confidence filler.
    """
    if selection_source == "safe_backfill":
        return "implied"

    strong_historical = bool(from_historical) and bool(historic_idmt_ids)
    strong_combined = bool(from_semantic) and bool(from_historical)
    strong_confidence = float(confidence or 0.0) >= _DIRECT_CONFIDENCE

    if strong_historical or strong_combined or strong_confidence:
        return "direct"
    return "implied"


__all__ = [
    "validate_value_stream_name",
    "derive_support_type",
]
