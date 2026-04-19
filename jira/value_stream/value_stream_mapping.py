"""Value stream mapping and canonicalization.

Resolves canonical value stream names from Jira issue links/themes,
optionally using Azure AI Search BM25 lookup for canonicalization.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .helpers import clean_value_stream_name

_AZURE_VS_RESOLVER: Optional["_AzureValueStreamResolver"] = None


# ---------------------------------------------------------------------------
# Dedup / normalization helpers
# ---------------------------------------------------------------------------

def _norm_vs(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _stem(token: str) -> str:
    """Collapse common English inflections so `pricing`≡`price`, `insights`≡`insight`,
    `management`≡`manage`. Iterates to a fixed point so compound suffixes collapse
    (e.g. `management` → `manage` → `manag`)."""
    cur = token
    while True:
        if len(cur) <= 3:
            return cur
        for suffix in ("ments", "ment", "ings", "ing", "ies", "ed", "es", "e", "s"):
            if cur.endswith(suffix) and len(cur) - len(suffix) >= 3:
                cur = cur[: -len(suffix)]
                break
        else:
            return cur


def _vs_tokens(name: str) -> set[str]:
    """Tokenize on any non-alphanumeric so `/`, `&`, `,` split (e.g. `request/inquiry`),
    then stem to tolerate plural/gerund variants."""
    raw = re.findall(r"[a-z0-9]+", (name or "").lower())
    return {_stem(t) for t in raw}


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (str(row.get("key") or ""), str(row.get("summary") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _dedupe_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in names:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = _norm_vs(clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


# ---------------------------------------------------------------------------
# Azure BM25 resolver (lazy singleton)
# ---------------------------------------------------------------------------

class _AzureValueStreamResolver:
    """BM25 resolver against Azure AI Search ValueStream nodes."""

    def __init__(self) -> None:
        from ... import config as app_config
        from ...clients.azure_search import AzureSearchClient

        self._index_name = str(app_config.AZURE_SEARCH_INDEX_NAME or "").strip()
        self._embedding_model = str(app_config.EMBEDDING_MODEL or "").strip()
        self._embedding_dimension = int(getattr(app_config, "EMBEDDING_DIMENSION", 0) or 0)
        self._has_valid_config = bool(
            self._index_name and self._index_name.lower() != "your-index-name"
            and self._embedding_model and self._embedding_model.lower() != "string"
            and self._embedding_dimension > 0
            and getattr(app_config, "AISEARCH_BASE_URL", "")
        )
        self._lookup_disabled = False
        self._client = AzureSearchClient()
        self._cache: dict[str, Optional[str]] = {}

    def resolve_one(self, name: str) -> Optional[str]:
        key = _norm_vs(name)
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        if not self._has_valid_config or self._lookup_disabled:
            self._cache[key] = name.strip() or None
            return self._cache[key]

        try:
            from ...clients.azure_search import IDPAISearchRequest

            request = IDPAISearchRequest(
                index_name=self._index_name,
                search_type="simple",
                search_text=name,
                top=15,
                filter_expression="node_type eq 'ValueStream'",
                enable_vector_search=False,
                hybrid_mode=None,
                vector_weight=0.0,
                text_weight=1.0,
                embedding_params={
                    "api_version": "2024-06-01",
                    "encoding_format": "float",
                    "dimensions": self._embedding_dimension,
                    "model": self._embedding_model,
                },
            )
            response = self._client.search(name, request=request)
            docs = list(getattr(response, "documents", []) or [])
        except Exception:
            self._lookup_disabled = True
            self._cache[key] = name.strip() or None
            return self._cache[key]

        # Prefer exact normalized match
        for doc in docs:
            candidate = str(doc.get("entity_name") or "").strip()
            if _norm_vs(candidate) == key:
                self._cache[key] = candidate
                return candidate

        # Fallback: bidirectional containment. Accept when either the candidate
        # is largely in the key (handles Jira-style prefixes like "APR 2.0 Onboard Partner")
        # or the key is largely in the candidate (handles shorter Jira labels
        # against more specific index names, e.g. "Order to Cash" vs
        # "Order to Cash for Group Coverage"). Punctuation-split tokens so
        # `request/inquiry` matches `request inquiry`.
        key_tokens = _vs_tokens(name)
        for doc in docs:
            candidate = str(doc.get("entity_name") or "").strip()
            if not candidate:
                continue
            cand_tokens = _vs_tokens(candidate)
            if not cand_tokens or not key_tokens:
                continue
            overlap = len(cand_tokens & key_tokens)
            cand_containment = overlap / len(cand_tokens)
            key_containment = overlap / len(key_tokens)
            if cand_containment >= 0.8 or key_containment >= 0.8:
                self._cache[key] = candidate
                return candidate

        self._cache[key] = None
        return None


def _get_azure_vs_resolver() -> Optional[_AzureValueStreamResolver]:
    global _AZURE_VS_RESOLVER
    if _AZURE_VS_RESOLVER is not None:
        return _AZURE_VS_RESOLVER
    try:
        _AZURE_VS_RESOLVER = _AzureValueStreamResolver()
    except Exception:
        _AZURE_VS_RESOLVER = None
    return _AZURE_VS_RESOLVER


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def canonicalize_value_stream_names(vs_names: list[str]) -> list[str]:
    """Dedupe and normalize value-stream names to canonical Azure entity_name
    values via BM25 lookup. Falls back to the cleaned raw name when a name
    cannot be resolved so callers receive one entry per input.
    """
    deduped = _dedupe_names(vs_names)
    if not deduped:
        return []

    resolver = _get_azure_vs_resolver()
    if resolver is None:
        return deduped

    canonical: list[str] = []
    for name in deduped:
        cleaned = clean_value_stream_name(name) or name
        resolved = resolver.resolve_one(cleaned) or resolver.resolve_one(name)
        canonical.append(resolved or cleaned)
    return _dedupe_names(canonical)


def is_valid_vs_name(name: str) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return False
    if re.fullmatch(r"[A-Z]{1,5}", cleaned):
        return False
    if re.match(r"^(CP|IVL)\s*\d", cleaned):
        return False
    if re.search(r"\b(20|21)\d{2}\b", cleaned):
        return False
    return (" " in cleaned) or len(cleaned) >= 4


def _normalize_theme_summary(summary: str) -> str:
    text = clean_value_stream_name((summary or "").strip())
    return re.sub(r"\s{2,}", " ", text).strip(" :-")


def resolve_value_stream_mapping(ticket_data: dict, classified_links: dict) -> dict[str, Any]:
    """Resolve value stream names/IDs from classified links or theme fallback."""
    vs_links = list((classified_links or {}).get("vs") or [])
    label_source = "jira_issuelinks"

    if not vs_links:
        themes = list((ticket_data or {}).get("themes") or [])
        if themes:
            normalized: list[dict] = []
            for theme in themes:
                issue_type = str(theme.get("issue_type") or "").strip().lower()
                if issue_type and "theme" not in issue_type:
                    continue

                summary_raw = str(theme.get("summary_raw") or theme.get("summary") or "")
                summary = _normalize_theme_summary(summary_raw)
                if not is_valid_vs_name(summary):
                    continue
                normalized.append({
                    "key": str(theme.get("key") or ""),
                    "summary": summary,
                    "summary_raw": summary_raw,
                    "status": str(theme.get("status") or ""),
                    "issue_type": str(theme.get("issue_type") or ""),
                })
            vs_links = normalized
            label_source = "jira_themes_fallback"

    vs_links = _dedupe_rows(vs_links)
    resolver = _get_azure_vs_resolver()

    verified_links: list[dict] = []
    per_link_names: list[str] = []
    for link in vs_links:
        raw_summary = str(link.get("summary") or "")
        cleaned = clean_value_stream_name(raw_summary) or raw_summary
        if resolver is None:
            resolved = cleaned
        else:
            resolved = (
                resolver.resolve_one(cleaned)
                or resolver.resolve_one(raw_summary)
                or cleaned
            )
        if not resolved:
            continue
        verified_links.append(link)
        per_link_names.append(resolved)

    vs_links = verified_links
    vs_names = _dedupe_names(per_link_names)
    vs_ids = [str(link.get("key") or "") for link in vs_links if str(link.get("key") or "")]
    vs_statuses = [str(link.get("status") or "") for link in vs_links]

    linked_value_streams = [
        {
            "id": str(link.get("key") or ""),
            "name": per_link_names[idx],
            "status": str(link.get("status") or ""),
            "summary_raw": str(link.get("summary_raw") or link.get("summary") or ""),
        }
        for idx, link in enumerate(vs_links)
    ]

    return {
        "vs_links": vs_links,
        "vs_ids": vs_ids,
        "vs_names": vs_names,
        "vs_statuses": vs_statuses,
        "linked_value_streams": linked_value_streams,
        "label_source": label_source,
    }


def resolve_value_stream_epics_mapping(ticket_data: dict, classified_links: dict) -> dict[str, Any]:
    """Extract value streams and their associated epics from ticket data."""
    vs_mapping = resolve_value_stream_mapping(ticket_data, classified_links)
    linked_value_streams = vs_mapping.get("linked_value_streams", [])

    epics_raw = list((ticket_data or {}).get("epics") or [])

    epics_normalized: list[dict] = []
    seen_epic_keys: set[str] = set()

    for epic in epics_raw:
        key = str(epic.get("key") or "").strip()
        if not key or key in seen_epic_keys:
            continue

        summary_raw = str(epic.get("summary_raw") or epic.get("summary") or "").strip()
        summary = _normalize_theme_summary(summary_raw) if summary_raw else ""

        epics_normalized.append({
            "id": key,
            "key": key,
            "name": summary,
            "summary": summary,
            "summary_raw": summary_raw,
            "status": str(epic.get("status") or "").strip(),
            "type": str(epic.get("type") or "epic").strip(),
        })
        seen_epic_keys.add(key)

    epics_deduped = _dedupe_rows([
        {
            "key": e.get("key"),
            "name": e.get("name"),
            "summary_raw": e.get("summary_raw"),
            "status": e.get("status"),
        }
        for e in epics_normalized
    ])

    vs_with_epics: list[dict] = []
    for vs in linked_value_streams:
        vs_with_epics.append({
            "value_stream": vs,
            "epics": epics_normalized,
            "epic_ids": [e["id"] for e in epics_normalized],
            "epic_count": len(epics_normalized),
        })

    return {
        "value_streams": linked_value_streams,
        "epics_normalized": epics_normalized,
        "epics_deduped": epics_deduped,
        "vs_with_epics": vs_with_epics,
        "all_epic_ids": [e.get("id") for e in epics_normalized],
        "all_epic_names": [e.get("name") for e in epics_normalized if e.get("name")],
        "summary": {
            "num_value_streams": len(linked_value_streams),
            "num_unique_epics": len({e.get("id") for e in epics_normalized}),
            "num_vs_with_epics": len([vs for vs in vs_with_epics if vs.get("epic_ids")]),
        },
    }
