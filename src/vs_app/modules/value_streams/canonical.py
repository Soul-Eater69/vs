from __future__ import annotations

import re

from vs_app.ingestion.jira.value_stream_labels.approved_registry import (
    canonicalize_approved_value_stream,
)


def normalize_vs_name(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_value_stream_key(name: str) -> str:
    """Normalized key used for value-stream dedup / merge / candidate lookup.

    Lowercase + whitespace-collapse only; punctuation is preserved. This is the
    shared definition of the key the RAG candidate merger and finalizer have each
    been computing locally, kept byte-for-byte identical to that prior behavior so
    dedup/lookup results do not shift.

    Distinct from :func:`normalize_vs_name` (which also folds ``&`` to ``and`` and
    strips punctuation for approved-registry canonicalization) — do not conflate
    the two: they serve different purposes and produce different keys.
    """
    return " ".join((name or "").strip().lower().split())


# Alias overrides that are not exact approved-registry names.
VALUE_STREAM_ALIAS_MAP: dict[str, str] = {
    "order to cash": "Order to Cash for Group Coverage",
}


DOMAIN_SIGNAL_EXPANSIONS: dict[str, list[str]] = {
    "ensure payment integrity": [
        "Ensure Payment Integrity",
        "Issue Payment",
        "Manage Invoice and Payment Receipt",
    ],
}


def canonicalize_value_stream_name(raw_name: str) -> str | None:
    key = normalize_vs_name(raw_name)
    if not key:
        return None
    return VALUE_STREAM_ALIAS_MAP.get(key) or canonicalize_approved_value_stream(raw_name)


def expand_domain_signal(raw_name: str) -> list[str]:
    key = normalize_vs_name(raw_name)
    return DOMAIN_SIGNAL_EXPANSIONS.get(key, [])
