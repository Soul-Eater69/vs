"""Cypher queries + schema-discovery helpers for Neo4j ticket reads.

No driver here, no execution. Pure strings and pure helpers that sanitize /
normalize / build query text. The `ticket_repository` module runs these.
"""

from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Schema defaults
# ---------------------------------------------------------------------------

DEFAULT_TICKET_LABEL = "JIRA"
DEFAULT_TICKET_KEY_PROPERTY = "key"
DEFAULT_GROUP_LINK_FIELD = "inwardIssues"
DEFAULT_ISSUE_TYPE_PROPERTY = "issueType"

THEME_ISSUE_TYPE = "Theme"
GROUP_KEY_PREFIX = "GROUP-"

KEY_PROPERTY_CANDIDATES = (
    "key", "ticketKey", "ticket_key", "issueKey", "issue_key", "jiraKey", "jira_key",
)
GROUP_LINK_FIELD_CANDIDATES = (
    "inwardIssues", "inward_issues", "linkedGroupKeys", "linked_group_keys",
    "groupKeys", "group_keys",
)
ISSUE_TYPE_PROPERTY_CANDIDATES = (
    "issueType", "issue_type", "type", "ticketType", "ticket_type",
)

# ---------------------------------------------------------------------------
# Fallback discovery queries (used when the configured schema doesn't match)
# ---------------------------------------------------------------------------

DISCOVERY_TICKET_QUERY = """
MATCH (t)
WHERE any(prop IN $key_properties WHERE toUpper(trim(toString(t[prop]))) = $ticket_key_upper)
RETURN t AS ticket_node
LIMIT 1
"""

DISCOVERY_THEMES_QUERY = """
MATCH (g)
WHERE any(prop IN $key_properties WHERE toUpper(trim(toString(g[prop]))) IN $group_keys_upper)
  AND any(prop IN $issue_type_properties WHERE toUpper(trim(toString(g[prop]))) = $theme_issue_type_upper)
RETURN g AS theme_node
"""


# ---------------------------------------------------------------------------
# Identifier sanitization + match helpers
# ---------------------------------------------------------------------------

def sanitize_identifier(value: str) -> str:
    """Return the value if it's a safe Cypher identifier (letters/digits/underscore), else ''."""
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else ""


def normalize_for_match(value: object) -> str:
    return str(value or "").strip().upper()


def candidate_property_names(primary: str, fallbacks: Iterable[str]) -> list[str]:
    """De-duplicated list of valid Cypher identifiers across primary + fallbacks."""
    names: list[str] = []
    seen: set[str] = set()
    for name in (primary, *fallbacks):
        cleaned = sanitize_identifier(name)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names


# ---------------------------------------------------------------------------
# Query builders (interpolate sanitized identifiers — callers must sanitize first)
# ---------------------------------------------------------------------------

def build_ticket_query(label: str, key_property: str) -> str:
    return f"""
MATCH (t:{label})
WHERE toUpper(trim(toString(t.{key_property}))) = $ticket_key_upper
RETURN t AS ticket_node
LIMIT 1
"""


def build_themes_query(
    label: str,
    key_property: str,
    issue_type_property: str,
) -> str:
    return f"""
MATCH (g:{label})
WHERE toUpper(trim(toString(g.{key_property}))) IN $group_keys_upper
  AND toUpper(trim(toString(g.{issue_type_property}))) = $theme_issue_type_upper
RETURN g AS theme_node
"""
