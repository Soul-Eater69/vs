"""Approved canonical value streams used by Jira link verification."""

from __future__ import annotations

import re

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

_NOISE_TOKENS = {"apr", "and"}


def _normalize_tokens(value: str) -> tuple[str, ...]:
    cleaned = clean_value_stream_name(value)
    raw_tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    return tuple(
        token
        for token in raw_tokens
        if token not in _NOISE_TOKENS and not re.fullmatch(r"\d+(?:\.\d+)?", token)
    )


_CANONICAL_BY_KEY: dict[str, str] = {}
_CANONICAL_TOKEN_SETS: dict[str, frozenset[str]] = {}
for _name in APPROVED_VALUE_STREAMS:
    _tokens = _normalize_tokens(_name)
    _key = " ".join(_tokens)
    if _key:
        _CANONICAL_BY_KEY[_key] = _name
        _CANONICAL_TOKEN_SETS[_name] = frozenset(_tokens)


def approved_value_streams_text() -> str:
    """Stable numbered list for prompt context."""
    return "\n".join(f"{idx}. {name}" for idx, name in enumerate(APPROVED_VALUE_STREAMS, start=1))


def canonicalize_approved_value_stream(value: str) -> str | None:
    """Map a Jira-style stream/theme name to the approved canonical list."""
    tokens = _normalize_tokens(value)
    if not tokens:
        return None

    direct = _CANONICAL_BY_KEY.get(" ".join(tokens))
    if direct:
        return direct

    target = frozenset(tokens)
    best_name: str | None = None
    best_score: tuple[float, int, int] | None = None
    ambiguous = False

    for canonical_name, canonical_tokens in _CANONICAL_TOKEN_SETS.items():
        overlap = len(target & canonical_tokens)
        if overlap == 0:
            continue

        canonical_containment = overlap / len(canonical_tokens)
        target_containment = overlap / len(target)
        if canonical_containment < 0.8 and target_containment < 0.8:
            continue

        score = (
            max(canonical_containment, target_containment),
            overlap,
            -abs(len(canonical_tokens) - len(target)),
        )
        if best_score is None or score > best_score:
            best_name = canonical_name
            best_score = score
            ambiguous = False
            continue

        if score == best_score and canonical_name != best_name:
            ambiguous = True

    if ambiguous:
        return None
    return best_name
