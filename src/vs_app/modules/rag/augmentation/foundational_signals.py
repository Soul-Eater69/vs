from __future__ import annotations

import re


FOUNDATIONAL_ALIAS_MAP: dict[str, list[str]] = {
    "establish product offering": [
        "Establish Product Offering",
    ],
    "order to cash": [
        "Order to Cash for Group Coverage",
    ],
    "order to cash for group coverage": [
        "Order to Cash for Group Coverage",
    ],
    "configure price and quote": [
        "Configure, Price, and Quote",
    ],
    "manage leads and opportunities": [
        "Manage Leads and Opportunities",
    ],
    "resolve request inquiry": [
        "Resolve Request-Inquiry",
    ],
    "resolve request-inquiry": [
        "Resolve Request-Inquiry",
    ],
    "perform engagement": [
        "Perform Engagement",
    ],
    "manage member care": [
        "Manage Member Care",
    ],
    "ensure payment integrity": [
        "Ensure Payment Integrity",
        "Issue Payment",
        "Manage Invoice and Payment Receipt",
    ],
}


FOUNDATIONAL_SIGNAL_DISPLAY: dict[str, str] = {
    "establish product offering": "Establish Product Offering",
    "order to cash": "Order to Cash",
    "order to cash for group coverage": "Order to Cash for Group Coverage",
    "configure price and quote": "Configure, Price and Quote",
    "manage leads and opportunities": "Manage Leads and Opportunities",
    "resolve request inquiry": "Resolve Request Inquiry",
    "resolve request-inquiry": "Resolve Request-Inquiry",
    "perform engagement": "Perform Engagement",
    "manage member care": "Manage Member Care",
    "ensure payment integrity": "Ensure Payment Integrity",
}


def normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _contains_phrase(text_norm: str, phrase_norm: str) -> bool:
    if not text_norm or not phrase_norm:
        return False
    return f" {phrase_norm} " in f" {text_norm} "


def extract_foundational_signals(idea_text: str) -> list[str]:
    """Extract foundational/current-card value-stream signals from idea text.

    This uses conservative phrase matching against known value-stream aliases.
    It is deterministic and does not auto-select anything.
    """
    text_norm = normalize_text(idea_text)
    if not text_norm:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for signal in FOUNDATIONAL_ALIAS_MAP:
        signal_norm = normalize_text(signal)
        if _contains_phrase(text_norm, signal_norm) and signal_norm not in seen:
            seen.add(signal_norm)
            found.append(FOUNDATIONAL_SIGNAL_DISPLAY.get(signal_norm, signal))

    return found


def annotate_foundational_signals(
    candidates: list[dict],
    idea_text: str,
) -> list[dict]:
    """Annotate candidates that match foundational/current-card signals.

    This does not auto-select and does not change candidate lane. It only adds
    metadata consumed by the Review Pool LLM prompt and UI/debug.
    """
    signals = extract_foundational_signals(idea_text)
    candidate_to_signal: dict[str, tuple[str, str]] = {}

    for signal in signals:
        signal_norm = normalize_text(signal)
        canonical_names = FOUNDATIONAL_ALIAS_MAP.get(signal_norm) or []

        for canonical_name in canonical_names:
            canonical_norm = normalize_text(canonical_name)
            match_type = "exact" if canonical_norm == signal_norm else "alias"
            candidate_to_signal[canonical_norm] = (signal, match_type)

    annotated: list[dict] = []
    for row in candidates:
        out = dict(row)
        name_norm = normalize_text(str(row.get("entity_name") or ""))

        if name_norm in candidate_to_signal:
            signal_text, match_type = candidate_to_signal[name_norm]
            out["foundational_signal"] = True
            out["foundational_match_text"] = signal_text
            out["foundational_match_type"] = match_type
        else:
            out.setdefault("foundational_signal", False)

        annotated.append(out)

    return annotated


def foundational_signal_names(idea_text: str) -> list[str]:
    return extract_foundational_signals(idea_text)
