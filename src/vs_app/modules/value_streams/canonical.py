from __future__ import annotations

import re
from dataclasses import dataclass

from vs_app.ingestion.jira.value_stream_labels.approved_registry import (
    approved_value_stream_id,
    canonicalize_approved_value_stream,
)


@dataclass(frozen=True)
class CanonicalValueStream:
    raw: str
    canonical_name: str
    entity_id: str | None = None
    match_type: str = "canonical"


def normalize_vs_name(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


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


def canonicalize_foundational_mentions(
    raw_mentions: list[str],
) -> list[CanonicalValueStream]:
    """Map raw ingestion mentions to canonical names.

    This is deterministic. It does not select candidates. It only normalizes metadata.
    """
    out: list[CanonicalValueStream] = []
    seen: set[str] = set()

    for raw in raw_mentions or []:
        raw_clean = " ".join(str(raw or "").split())
        if not raw_clean:
            continue

        canonical = canonicalize_value_stream_name(raw_clean)
        if canonical:
            key = normalize_vs_name(canonical)
            if key not in seen:
                seen.add(key)
                match_type = (
                    "exact"
                    if normalize_vs_name(raw_clean) == normalize_vs_name(canonical)
                    else "alias"
                )
                out.append(
                    CanonicalValueStream(
                        raw=raw_clean,
                        canonical_name=canonical,
                        entity_id=approved_value_stream_id(canonical) or None,
                        match_type=match_type,
                    )
                )

        for expanded in expand_domain_signal(raw_clean):
            key = normalize_vs_name(expanded)
            if key not in seen:
                seen.add(key)
                out.append(
                    CanonicalValueStream(
                        raw=raw_clean,
                        canonical_name=expanded,
                        entity_id=approved_value_stream_id(expanded) or None,
                        match_type="domain_signal",
                    )
                )

    return out
