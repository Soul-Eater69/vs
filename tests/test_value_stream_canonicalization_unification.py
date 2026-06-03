"""Golden-key tests for Value Stream canonicalization unification.

`normalize_value_stream_key` consolidates the dedup/merge/lookup key that the RAG
candidate merger (`_norm_name`) and finalizer (`_norm_key`) each computed locally.
These tests pin it byte-for-byte to the prior behavior, and lock in that the two
*different* normalizers (`normalize_vs_name`, reranker `_norm`) are intentionally
NOT folded in — merging them would change behavior.
"""

from __future__ import annotations

import re

from vs_app.modules.rag.augmentation.candidate_merger import (
    GENERIC_OR_RISKY_STREAMS,
    CandidateWindowPolicy,
    _norm_name,
    merge_candidate_sources,
)
from vs_app.modules.rag.augmentation.finalizer import _norm_key
from vs_app.modules.value_streams.canonical import (
    normalize_value_stream_key,
    normalize_vs_name,
)


# A name corpus covering casing, whitespace, punctuation, ampersands, slashes,
# hyphens, commas, aliases, generic/broad streams, and exact approved names.
CORPUS = [
    "Order to Cash",
    "Order to Cash, for Group Coverage",
    "  Configure,   Price, and Quote  ",
    "Configure, Price, and Quote",
    "Care & Health",
    "Resolve Request-Inquiry",
    "A/B Testing",
    "Receive Care",
    "Discover Business Insights",
    "Develop Mission, Vision, and Strategy",
    "MANAGE leads AND opportunities",
    "",
    "   ",
]


def _old_local_behavior(value: str) -> str:
    """The exact prior body of both _norm_name and _norm_key."""
    return " ".join((value or "").strip().lower().split())


def test_key_matches_prior_local_behavior_across_corpus() -> None:
    for name in CORPUS:
        assert normalize_value_stream_key(name) == _old_local_behavior(name), name


def test_local_wrappers_now_delegate_to_shared_key() -> None:
    for name in CORPUS:
        expected = _old_local_behavior(name)
        assert _norm_name(name) == expected, name
        assert _norm_key(name) == expected, name
        assert _norm_name(name) == _norm_key(name) == normalize_value_stream_key(name)


def test_key_preserves_punctuation_and_ampersand() -> None:
    # The defining property of this key: it does NOT strip punctuation or fold '&'.
    assert normalize_value_stream_key("Care & Health") == "care & health"
    assert normalize_value_stream_key("Configure, Price, and Quote") == "configure, price, and quote"
    assert normalize_value_stream_key("A/B Testing") == "a/b testing"


def test_generic_risky_set_membership_still_matches() -> None:
    # GENERIC_OR_RISKY_STREAMS entries (some with commas) are matched via this key,
    # so the key must preserve punctuation exactly as those entries are written.
    assert "develop mission, vision, and strategy" in GENERIC_OR_RISKY_STREAMS
    assert normalize_value_stream_key("Develop Mission, Vision, and Strategy") in GENERIC_OR_RISKY_STREAMS
    assert normalize_value_stream_key("Receive Care") in GENERIC_OR_RISKY_STREAMS


def test_key_is_intentionally_different_from_canonicalization_normalizer() -> None:
    # normalize_vs_name (approved-registry canonicalization) folds '&'->'and' and
    # strips punctuation; the dedup key does not. They must stay distinct.
    assert normalize_vs_name("Care & Health") == "care and health"
    assert normalize_value_stream_key("Care & Health") == "care & health"
    assert normalize_vs_name("Care & Health") != normalize_value_stream_key("Care & Health")


def _reranker_norm(text: str) -> str:
    # Mirror of reranker._norm (behavior C) — strips punctuation but does not fold '&'.
    text = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower().strip())
    return re.sub(r"\s+", " ", text).strip()


def test_key_is_intentionally_different_from_reranker_norm() -> None:
    # Reranker fuzzy-match normalization strips punctuation; the dedup key keeps it.
    assert _reranker_norm("Resolve Request-Inquiry") == "resolve request inquiry"
    assert normalize_value_stream_key("Resolve Request-Inquiry") == "resolve request-inquiry"
    assert _reranker_norm("Resolve Request-Inquiry") != normalize_value_stream_key("Resolve Request-Inquiry")


def test_candidate_merger_still_dedupes_same_name_different_casing_whitespace() -> None:
    semantic = [
        {"entity_id": "VS1", "entity_name": "Configure, Price, and Quote", "semantic_score": 1.5},
        {"entity_id": "VS1", "entity_name": "  configure,   price, and quote ", "semantic_score": 1.4},
    ]
    out = merge_candidate_sources(semantic, [], policy=CandidateWindowPolicy())
    merged_names = [r["entity_name"] for r in out["merged_candidates"]]
    # The two spellings collapse to a single merged candidate via the shared key.
    assert len(out["merged_candidates"]) == 1, merged_names
