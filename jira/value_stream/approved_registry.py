"""Approved canonical value streams used by Jira link verification."""

from __future__ import annotations

import re

from rapidfuzz import fuzz, process

from .helpers import clean_value_stream_name

APPROVED_VALUE_STREAMS: tuple[str, ...] = (
    "Acquire Asset",
    "Adjudicate Claim",
    "Align and Execute IT Strategy",
    "Configure, Price, and Quote",
    "Detect and Correct IT Issues",
    "Develop Human Resource Career",
    "Ensure Compliance",
    "Establish Product Offering",
    "Establish Provider Network",
    "Establish Provider Program",
    "File Regulatory Reports",
    "Fulfill Request for IT Support",
    "Fulfill Value-Based Care Arrangement",
    "Issue Payment",
    "Manage Claim Inventory",
    "Manage Invoice and Payment Receipt",
    "Manage Leads and Opportunities",
    "Manage Member Care",
    "Manage Producer Operations",
    "Onboard Human Resource",
    "Onboard Partner",
    "Optimize Reserves",
    "Order to Cash for Group Coverage",
    "Participate in Health Management Program",
    "Promote Community Health",
    "Receive Care",
    "Resolve Request-Inquiry",
    "Sell and Enroll Individual Coverage",
    "Support External Audit",
    "Ensure Payment Integrity",
    "Discover Business Insights",
    "Appeal Decision",
    "Perform Engagement",
    "Reconcile Data",
    "Reconcile Account",
    "Administer Quality Management Program",
    "Administer Utilization Management Program",
    "Manage Utilization Management Program",
    "Fill and Manage Prescriptions",
    "Realize Risk Adjustment",
    "Enroll Group Medicare Coverage",
    "Enroll Medicaid Member",
    "Manage Workforce",
    "Record Financial Transaction",
    "Recover Overpayment",
    "Pay Employee",
    "Conduct Audit",
    "Manage Enterprise Risk",
    "Resolve Privacy Incident",
    "Develop Mission, Vision, and Strategy",
)

APPROVED_VALUE_STREAM_SET = frozenset(APPROVED_VALUE_STREAMS)
_FUZZ_MATCH_THRESHOLD = 92.0
_FUZZ_AMBIGUITY_MARGIN = 2.0

_NOISE_TOKENS = {"apr", "and"}


def _normalize_tokens(value: str) -> tuple[str, ...]:
    cleaned = clean_value_stream_name(value)
    raw_tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    return tuple(
        token
        for token in raw_tokens
        if token not in _NOISE_TOKENS and not re.fullmatch(r"\d+(?:\.\d+)?", token)
    )


def _normalize_lookup_key(value: str) -> str:
    return " ".join(_normalize_tokens(value))


_CANONICAL_BY_KEY: dict[str, str] = {}
_LOOKUP_KEYS: list[str] = []
for _name in APPROVED_VALUE_STREAMS:
    _key = _normalize_lookup_key(_name)
    if _key:
        _CANONICAL_BY_KEY[_key] = _name
        _LOOKUP_KEYS.append(_key)


def approved_value_streams_text() -> str:
    """Stable numbered list for prompt context."""
    return "\n".join(f"{idx}. {name}" for idx, name in enumerate(APPROVED_VALUE_STREAMS, start=1))


def canonicalize_approved_value_stream(value: str) -> str | None:
    """Map a Jira-style stream/theme name to the approved canonical list."""
    lookup_key = _normalize_lookup_key(value)
    if not lookup_key:
        return None

    direct = _CANONICAL_BY_KEY.get(lookup_key)
    if direct:
        return direct

    matches = process.extract(
        lookup_key,
        _LOOKUP_KEYS,
        scorer=fuzz.token_set_ratio,
        limit=2,
    )
    if not matches:
        return None

    best_key, best_score, _ = matches[0]
    if float(best_score) < _FUZZ_MATCH_THRESHOLD:
        return None

    if len(matches) > 1:
        second_key, second_score, _ = matches[1]
        if (
            _CANONICAL_BY_KEY.get(second_key) != _CANONICAL_BY_KEY.get(best_key)
            and float(second_score) >= float(best_score) - _FUZZ_AMBIGUITY_MARGIN
        ):
            return None

    return _CANONICAL_BY_KEY.get(best_key)
