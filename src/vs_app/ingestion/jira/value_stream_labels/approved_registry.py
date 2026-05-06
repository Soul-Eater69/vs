"""Approved canonical value streams used by Jira link verification."""

from __future__ import annotations

from difflib import SequenceMatcher
import re

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover - optional dependency in local dev
    fuzz = None
    process = None

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
APPROVED_VALUE_STREAM_IDS: dict[str, str] = {
    "Acquire Asset": "VSR00074583",
    "Adjudicate Claim": "VSR00074584",
    "Align and Execute IT Strategy": "VSR00074585",
    "Configure, Price, and Quote": "VSR00074586",
    "Detect and Correct IT Issues": "VSR00074587",
    "Develop Human Resource Career": "VSR00074588",
    "Ensure Compliance": "VSR00074589",
    "Establish Product Offering": "VSR00074590",
    "Establish Provider Network": "VSR00074591",
    "Establish Provider Program": "VSR00074592",
    "File Regulatory Reports": "VSR00074593",
    "Fulfill Request for IT Support": "VSR00074594",
    "Fulfill Value-Based Care Arrangement": "VSR00074595",
    "Issue Payment": "VSR00074596",
    "Manage Claim Inventory": "VSR00074597",
    "Manage Invoice and Payment Receipt": "VSR00074598",
    "Manage Leads and Opportunities": "VSR00074599",
    "Manage Member Care": "VSR00074600",
    "Manage Producer Operations": "VSR00074601",
    "Onboard Human Resource": "VSR00074602",
    "Onboard Partner": "VSR00074603",
    "Optimize Reserves": "VSR00074604",
    "Order to Cash for Group Coverage": "VSR00074605",
    "Participate in Health Management Program": "VSR00074606",
    "Promote Community Health": "VSR00074607",
    "Receive Care": "VSR00074608",
    "Resolve Request-Inquiry": "VSR00074609",
    "Sell and Enroll Individual Coverage": "VSR00074610",
    "Support External Audit": "VSR00074611",
    "Ensure Payment Integrity": "VSR00167984",
    "Discover Business Insights": "VSR00168101",
    "Appeal Decision": "VSR00168121",
    "Perform Engagement": "VSR00168122",
    "Reconcile Data": "VSR00168123",
    "Reconcile Account": "VSR00168124",
    "Administer Quality Management Program": "VSR00168128",
    "Administer Utilization Management Program": "VSR00168129",
    "Manage Utilization Management Program": "VSR00168130",
    "Fill and Manage Prescriptions": "VSR00168131",
    "Realize Risk Adjustment": "VSR00168132",
    "Enroll Group Medicare Coverage": "VSR00168134",
    "Enroll Medicaid Member": "VSR00168135",
    "Manage Workforce": "VSR00168137",
    "Record Financial Transaction": "VSR01261891",
    "Recover Overpayment": "VSR01261892",
    "Pay Employee": "VSR01261893",
    "Conduct Audit": "VSR01261894",
    "Manage Enterprise Risk": "VSR01261895",
    "Resolve Privacy Incident": "VSR01261896",
    "Develop Mission, Vision, and Strategy": "VSR01261897",
}
_FUZZ_MATCH_THRESHOLD = 90.0
_FUZZ_AMBIGUITY_MARGIN = 2.0
_MIN_SUFFIX_TOKENS = 2

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


def _candidate_lookup_keys(value: str) -> list[str]:
    lookup_key = _normalize_lookup_key(value)
    if not lookup_key:
        return []

    parts = lookup_key.split()
    candidates: list[str] = []
    seen: set[str] = set()

    for start in range(0, len(parts)):
        suffix = " ".join(parts[start:])
        if len(suffix.split()) < _MIN_SUFFIX_TOKENS and start != 0:
            continue
        if suffix in seen:
            continue
        seen.add(suffix)
        candidates.append(suffix)

    return candidates


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


def approved_value_stream_id(value: str) -> str:
    canonical = canonicalize_approved_value_stream(value)
    if not canonical:
        return ""
    return APPROVED_VALUE_STREAM_IDS.get(canonical, "")


def _extract_matches(lookup_key: str, *, limit: int = 3) -> list[tuple[str, float, int | None]]:
    if process is not None and fuzz is not None:
        return process.extract(
            lookup_key,
            _LOOKUP_KEYS,
            scorer=fuzz.WRatio,
            limit=limit,
        )

    sorted_key = " ".join(sorted(lookup_key.split()))
    ranked: list[tuple[str, float, int | None]] = []
    for idx, candidate in enumerate(_LOOKUP_KEYS):
        direct = SequenceMatcher(None, lookup_key, candidate).ratio() * 100.0
        token_sort = SequenceMatcher(None, sorted_key, " ".join(sorted(candidate.split()))).ratio() * 100.0
        short, long_ = (lookup_key, candidate) if len(lookup_key) <= len(candidate) else (candidate, lookup_key)
        partial = max(
            SequenceMatcher(None, short, long_[i : i + len(short)]).ratio() * 100.0
            for i in range(len(long_) - len(short) + 1)
        ) if long_ else 0.0
        ranked.append((candidate, max(direct, token_sort, partial), idx))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked[:limit]


def canonicalize_approved_value_stream(value: str) -> str | None:
    """Map a Jira-style stream/theme name to the approved canonical list."""
    candidate_keys = _candidate_lookup_keys(value)
    if not candidate_keys:
        return None

    for lookup_key in candidate_keys:
        direct = _CANONICAL_BY_KEY.get(lookup_key)
        if direct:
            return direct

    best_scores: dict[str, float] = {}
    for lookup_key in candidate_keys:
        matches = _extract_matches(lookup_key, limit=3)
        for matched_key, score, _ in matches:
            canonical_name = _CANONICAL_BY_KEY.get(matched_key)
            if canonical_name is None:
                continue
            best_scores[canonical_name] = max(best_scores.get(canonical_name, 0.0), float(score))

    if not best_scores:
        return None

    ranked = sorted(best_scores.items(), key=lambda item: item[1], reverse=True)
    best_name, best_score = ranked[0]
    if best_score < _FUZZ_MATCH_THRESHOLD:
        return None

    if len(ranked) > 1 and ranked[1][1] >= best_score - _FUZZ_AMBIGUITY_MARGIN:
        return None

    return best_name
