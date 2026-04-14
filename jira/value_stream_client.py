"""
Async Jira client with MarkItDown-powered attachment extraction.

Usage:
    client = JiraValueStreamClient(base_url, token, verify_ssl=False)
    await client.authenticate()
    data = await client.get_ticket_data("IDEA-1234")
    contents = await client.fetch_attachment_content(data["attachments"])
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from difflib import SequenceMatcher
from hashlib import sha256
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

import httpx
from core.jira_client.rest import JIRARestClient as CoreJIRARestClient

logger = logging.getLogger(__name__)

_ATTACHMENT_FETCH_RETRIES = 4
_ATTACHMENT_CONVERT_TIMEOUT_SEC = 90.0  # max seconds for markitdown to parse one file
_ATTACHMENT_FETCH_BASE_DELAY_SEC = 1.0
_DESC_WIKI_LINK_RE = re.compile(r"\[(?P<label>[^|]+)\|(?P<url>https?://[^\]]+)\]")
_DESC_MD_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)")
_DESC_BARE_FILE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>\"']+\.(?P<ext>pptx|ppt|pdf|docx|doc|xlsx|xls|csv|png|jpg|jpeg|gif|bmp|tiff|webp)(?:\?[^\s<>\"']*)?)", re.IGNORECASE)

_EXT_TO_MIME: dict[str, str] = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}

def _clean_value_stream_name(summary: str) -> str:
    """Normalize linked theme/value-stream summaries to just the theme name."""
    text = (summary or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^(?:(?:GROUP-\d+(?:,\s*|\s*(?:&|and)\s*|\s+)*)+|THEME\s*#?\s*\d+)(?:\s*\([^)]+\))?\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*-\s*(?:IVL(?:\s|)[A-Z]+-\d+|APP\d{5,}|P\d{5,}).*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" -:")

# -----------------------------------------------------------------------------
# Base protocol
# -----------------------------------------------------------------------------

class ValueStreamFetcher(ABC):
    """Interface that any value stream data source must implement."""

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def get_ticket_data(self, ticket_id: str, config: Optional[Any] = None) -> Dict[str, Any]: ...

    @abstractmethod
    async def fetch_attachment_content(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> Any: ...


# -----------------------------------------------------------------------------
# Low-level REST client
# -----------------------------------------------------------------------------

class JIRARestClient(CoreJIRARestClient):
    """Value-stream Jira client built on the shared core Jira REST client."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        api_token: str,
        verify_ssl: bool = True,
    ) -> None:
        super().__init__(
            base_url=base_url,
            auth_token=auth_token,
            api_token=api_token,
            verify_ssl=verify_ssl,
            jira_api_version="2",
            timeout=60.0,
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def _authenticate(self) -> None:
        """Create an authenticated httpx client and verify credentials."""
        self._client = httpx.AsyncClient(
            verify=self.verify_ssl,
            headers={**self._get_headers(), "Accept": "application/json"},
            timeout=60.0,
        )
        response = await self._client.get(
            f"{self.base_url}/rest/api/{self.jira_api_version}/myself"
        )
        response.raise_for_status()
        me = response.json()
        logger.info(f"Authenticated as %s", me.get("displayName", me.get("name")))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_issue_by_key(
        self,
        key: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Fetch an issue by key, optionally restricting to `fields`."""
        if self._client is None:
            raise RuntimeError("Call .authenticate() first.")
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issue/{key}"
        params: Dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# -----------------------------------------------------------------------------
# High-level value-stream client
# -----------------------------------------------------------------------------

class JiraValueStreamClient(ValueStreamFetcher):
    """Async Jira client that exposes ticket data and attachment text extraction."""

    def __init__(self, base_url: str, token: str, verify_ssl: bool = False) -> None:
        self.base_url = base_url
        self.token = token
        self.verify_ssl = verify_ssl
        self.client = JIRARestClient(
            base_url=base_url,
            auth_token=token,
            api_token="",
            verify_ssl=verify_ssl,
        )

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def authenticate(self) -> None:
        await self.client._authenticate()

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> "JiraValueStreamClient":
        await self.authenticate()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Core API calls
    # -------------------------------------------------------------------------

    # Non-custom base fields - always requested regardless of tenant config.
    _BASE_FIELDS: List[str] = [
        "summary", "description", "reporter", "assignee",
        "created", "updated", "status", "priority", "issuetype",
        "labels", "components", "attachment", "issuelinks",
        "comment", "parent", "subtasks",
    ]

    def _build_ticket_fields(self, config: Optional[Any] = None) -> List[str]:
        """
        Build the Jira fields list from base fields + configured custom fields.

        Custom field IDs come from config.jira_field_map so the client stays
        portable across Jira tenants without code changes.
        """
        base = list(self._BASE_FIELDS)
        jira_field_map: dict = getattr(config, "jira_field_map", {}) if config else {}
        custom = sorted(
            v for v in jira_field_map.values()
            if isinstance(v, str) and v.startswith("customfield_")
        )
        return base + custom

    async def get_ticket_data(self, ticket_id: str, config: Optional[Any] = None) -> Dict[str, Any]:
        """
        Fetch a Jira ticket and return structured data with:
          - key:         Ticket key (e.g. "IDEA-1234")
          - fields:      All ticket fields (summary, description, links, etc.)
          - attachments: List of attachment metadata dicts
          - themes:      List of linked issues (key, summary, status)
        """
        issue = await self.client.get_issue_by_key(
            ticket_id, fields=self._build_ticket_fields(config)
        )
        fields = issue.get("fields", {})

        attachments = fields.get("attachment", [])
        description_attachments = self._extract_description_link_attachments(fields.get("description"))

        existing_keys = {
            str(att.get("id", "")).strip().lower(),
            str(att.get("filename") or "").strip().lower(),
        }
        for att in attachments:
            existing_keys.add(str(att.get("filename") or "").strip().lower())

        merged_attachments = list(attachments)
        for att in description_attachments:
            key = (
                str(att.get("content") or "").strip().lower(),
                str(att.get("filename") or "").strip().lower()
            )
            if key in existing_keys:
                continue
            merged_attachments.append(att)
            existing_keys.add(key)

        # -- Themes: Linked issues filtered by "Implements" relationship --
        themes: List[Dict[str, Any]] = []
        issuelinks = fields.get("issuelinks", [])
        for link in issuelinks:
            if (
                link.get("type", {}).get("outward") == "Implements"
                and "outwardIssue" in link
            ):
                fields_data = link["outwardIssue"].get("fields", {})
                summary_raw = fields_data.get("summary") or ""
                themes.append({
                    "key": link["outwardIssue"].get("key"),
                    "summary": _clean_value_stream_name(summary_raw),
                    "summary_raw": summary_raw,
                    "status": fields_data.get("status", {}).get("name"),
                })

            if (
                link.get("type", {}).get("inward") == "is implemented by"
                and "inwardIssue" in link
            ):
                fields_data = link["inwardIssue"].get("fields", {})
                summary_raw = fields_data.get("summary") or ""
                themes.append({
                    "key": link["inwardIssue"].get("key"),
                    "summary": _clean_value_stream_name(summary_raw),
                    "summary_raw": summary_raw,
                    "status": fields_data.get("status", {}).get("name"),
                })

        # Align VS extraction with ingestion/v4 flow:
        # classify_links -> resolve_value_stream_mapping -> canonicalized names.
        try:
            from ingestion.metadata import classify_links
            from ingestion.value_stream_mapping_service import resolve_value_stream_mapping

            classified_links = classify_links(issuelinks)
            vs_mapping = resolve_value_stream_mapping({"themes": themes}, classified_links)
            value_stream_names = list(vs_mapping.get("linked_value_streams") or [])
            value_stream_ids = list(vs_mapping.get("vs_ids") or [])
            value_stream_statuses = list(vs_mapping.get("vs_statuses") or [])
            value_stream_label_source = str(vs_mapping.get("label_source") or "jira_issuelinks")
        except Exception:
            # Safe fallback to local themes-only representation
            value_streams = [
                {
                    "id": str(theme.get("key") or ""),
                    "name": str(theme.get("summary") or ""),
                    "status": str(theme.get("status") or ""),
                    "summary_raw": str(theme.get("summary_raw") or theme.get("summary") or ""),
                }
                for theme in themes
            ]
            value_stream_names = [str(v.get("name") or "") for v in value_streams if str(v.get("name") or "")]
            value_stream_ids = [str(v.get("id") or "") for v in value_streams if str(v.get("id") or "")]
            value_stream_statuses = [str(v.get("status") or "") for v in value_streams]
            value_stream_label_source = "jira_themes_fallback"

        # -- Epics: configured epic field + parent + hierarchy links --
        epics: List[Dict[str, Any]] = self._extract_epics(issue, config=config)

        # Map value streams > epics (ticket level)
        value_stream_epics = self._map_value_streams_to_epics(value_stream_names, epics)

        return {
            "key": issue.get("key", ticket_id),
            "fields": fields,
            "attachments": merged_attachments,
            "description_attachments": description_attachments,
            "themes": themes,
            "epics": epics,
            "value_stream_names": value_stream_names,
            "value_stream_ids": value_stream_ids,
            "value_stream_statuses": value_stream_statuses,
            "value_stream_label_source": value_stream_label_source,
            "value_stream_epics": value_stream_epics,
        }

    def _extract_epics(self, issue: Dict[str, Any], config: Optional[Any] = None) -> List[Dict[str, Any]]:
        """
        Extract epics from an issue by checking:
        1. Epic-link relationships in issuelinks
        2. Epic-link custom fields (if available)

        Returns list of epic dicts with keys: key, summary, summary_raw, status
        """
        epics: List[Dict[str, Any]] = []
        fields = issue.get("fields", {})

        jira_field_map: dict[str, str] = getattr(config, "jira_field_map", {}) if config else {}
        epic_field_id = jira_field_map.get("epic_link", "customfield_10014")

        def _append_epic(
            key: str,
            summary_raw: str,
            status: str,
            source: str,
            issue_type: str = "",
        ) -> None:
            cleaned_key = str(key or "").strip()
            cleaned_raw = str(summary_raw or "").strip()
            cleaned_status = str(status or "").strip()
            if not cleaned_key and not cleaned_raw:
                return
            epics.append(
                {
                    "key": cleaned_key,
                    "summary": _clean_value_stream_name(cleaned_raw),
                    "summary_raw": cleaned_raw,
                    "status": cleaned_status,
                    "source": source,
                    "issue_type": str(issue_type or "").strip(),
                }
            )

        # -- Explicit epic link field (configured field ID only) --
        epic_link_value = fields.get(epic_field_id)
        if isinstance(epic_link_value, str) and epic_link_value.strip():
            _append_epic(epic_link_value, "", "", "customfield")
        elif isinstance(epic_link_value, dict):
            _append_epic(
                key=str(epic_link_value.get("key") or ""),
                summary_raw=str(epic_link_value.get("summary") or ""),
                status=str((epic_link_value.get("status") or {}).get("name") or ""),
                source="customfield",
                issue_type=str((epic_link_value.get("issuetype") or {}).get("name") or ""),
            )

        # -- Parent field (next-gen Jira) --
        parent = fields.get("parent")
        if isinstance(parent, dict):
            parent_fields = parent.get("fields", {})
            _append_epic(
                key=str(parent.get("key") or ""),
                summary_raw=str(parent_fields.get("summary") or ""),
                status=str((parent_fields.get("status") or {}).get("name") or ""),
                source="parent",
                issue_type=str((parent_fields.get("issuetype") or {}).get("name") or ""),
            )

        # -- Check hierarchy links for epic relationships --
        issuelinks = fields.get("issuelinks", [])
        for link in issuelinks:
            link_type = link.get("type", {}) or {}
            link_name = str(link_type.get("name") or "").lower()
            outward = str(link_type.get("outward") or "").lower()
            inward = str(link_type.get("inward") or "").lower()

            for direction in ("outwardIssue", "inwardIssue"):
                linked_issue = link.get(direction)
                if not isinstance(linked_issue, dict):
                    continue

                linked_fields = linked_issue.get("fields", {})
                issue_type = str((linked_fields.get("issuetype") or {}).get("name") or "").lower()
                summary_raw = str(linked_fields.get("summary") or "")
                key = str(linked_issue.get("key") or "")
                status = str((linked_fields.get("status") or {}).get("name") or "")

                is_hierarchy = (
                    "epic" in link_name
                    or "parent" in link_name
                    or "child" in link_name
                    or outward in ("is child of", "has epic", "parent")
                    or inward in ("is parent of", "is epic of", "child")
                )

                # Keep likely epic records; avoid pulling unrelated links.
                likely_epic = (
                    issue_type.lower() == "epic"
                    or "epic" in issue_type.lower()
                    or "epic" in link_name
                )

                if not (likely_epic or is_hierarchy):
                    continue

                if likely_epic or key:
                    _append_epic(key, summary_raw, status, "link", issue_type)


        # -- Child issues / subtasks (classic Jira subtasks & next-gen children) --
        # For value-stream GROUP tickets, child issues ARE the epics.
        for subtask in fields.get("subtasks") or []:
            if not isinstance(subtask, dict):
                continue
            sub_fields = subtask.get("fields") or {}
            _append_epic(
                key=str(subtask.get("key") or ""),
                summary_raw=str(sub_fields.get("summary") or ""),
                status=str((sub_fields.get("status") or {}).get("name") or ""),
                source="subtask",
                issue_type=str((sub_fields.get("issuetype") or {}).get("name") or ""),
            )

        # Dedupe epics by key first, then by normalized summary
        deduped: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        seen_names: set[str] = set()

        for epic in epics:
            key = str(epic.get("key") or "").strip().lower()
            name = str(epic.get("summary") or epic.get("summary_raw") or "").strip().lower()

            if key and key in seen_keys:
                continue

            if not key and name and name in seen_names:
                continue

            if key:
                seen_keys.add(key)
            if name:
                seen_names.add(name)

            deduped.append(epic)

        return deduped

    @staticmethod
    def _map_value_streams_to_epics(
        value_streams: List[str],
        epics: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Map ticket epics to value streams using normalized name similarity.

        Strategy:
          1. For each epic, find best matching value stream by max(similarity, token overlap)
          2. If score >= 0.40, assign epic to that value stream
          3. If only one value stream exists, assign all remaining epics to it
        """
        if not value_streams:
            return []

        def _norm_name(text: str) -> str:
            cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
            return re.sub(r"\s+", " ", cleaned).strip()

        buckets: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []

        for vs in value_streams:
            vs_id = str(vs.get("id") or "").strip()
            vs_name = str(vs.get("name") or "").strip()
            bucket_key = vs_id or vs_name
            if not bucket_key:
                continue

            if bucket_key not in buckets:
                buckets[bucket_key] = {
                    "value_stream": {
                        "id": vs_id,
                        "name": vs_name,
                        "status": str(vs.get("status") or "").strip(),
                    },
                    "epics": [],
                    "epic_ids": [],
                    "epic_count": 0,
                }
                order.append(bucket_key)

        if not buckets:
            return []

        for epic in epics:
            epic_name = str(epic.get("summary") or epic.get("summary_raw") or "").strip()
            epic_key = str(epic.get("key") or "").strip()
            epic_norm = _norm_name(epic_name)

            best_bucket = None
            best_score = 0.0

            for bucket_key in order:
                vs_name = str(buckets[bucket_key]["value_stream"].get("name") or "").strip()
                vs_norm = _norm_name(vs_name)
                if not vs_norm and not epic_norm:
                    continue

                seq_score = SequenceMatcher(None, epic_norm, vs_norm).ratio() if (epic_norm and vs_norm) else 0.0
                epic_tokens = set(epic_norm.split()) if epic_norm else set()
                vs_tokens = set(vs_norm.split()) if vs_norm else set()
                overlap = len(epic_tokens & vs_tokens) / max(len(vs_tokens), 1) if vs_tokens else 0.0
                score = max(seq_score, overlap)

                if score > best_score:
                    best_score = score
                    best_bucket = bucket_key

            assign_bucket = None
            if best_bucket and best_score >= 0.40:
                assign_bucket = best_bucket
            elif len(order) == 1:
                assign_bucket = order[0]

            if assign_bucket is None:
                continue

            bucket = buckets[assign_bucket]
            duplicate = any(str(e.get("key") or "") == epic_key for e in bucket["epics"] if epic_key)
            if duplicate:
                continue
            bucket["epics"].append(epic)
            if epic_key:
                bucket["epic_ids"].append(epic_key)

        result: List[Dict[str, Any]] = []
        for bucket_key in order:
            bucket = buckets[bucket_key]
            bucket["epic_count"] = len(bucket["epics"])
            result.append(bucket)

        return result

    @staticmethod
    def verify_epic_candidates(
        extracted_epics: List[Dict[str, Any]],
        canonical_candidates: List[Dict[str, Any]],
        fuzzy_threshold: float = 0.75,
    ) -> Dict[str, Any]:
        """
        Cross-verify extracted epics against canonical value stream candidates.

        Adapted from src.pipelines.value_stream.retrieval_pipeline.sanitize_selected(),
        this function matches extracted epic names against a list of canonical candidates
        using multiple matching strategies:
        1. Exact entity ID match (if epic has an ID)
        2. Exact name match (case-insensitive)
        3. Normalized string match (alphanumeric only)
        4. Fuzzy matching with SequenceMatcher (configurable threshold, default 0.75)

        Args:
            extracted_epics: List of dicts with keys (key, summary, summary_raw, status, type)
            canonical_candidates: List of dicts with keys (entity_id, entity_name, ...)
            fuzzy_threshold: Minimum SequenceMatcher ratio for fuzzy matches (0.0 to 1.0)

        Returns:
            Dict with keys:
            - verified: List of matched epics with candidate details
            - unmatched: List of extracted epics that couldn't be matched
            - summary: Count stats (total_input, verified_count, unmatched_count)
        """
        from difflib import SequenceMatcher

        # Build lookup indexes for fast candidate matching
        by_name = {
            str(c.get("entity_name", "")).strip().lower(): c
            for c in canonical_candidates
            if c.get("entity_name")
        }

        by_id = {
            str(c.get("entity_id") or "").strip().lower(): c
            for c in canonical_candidates
            if str(c.get("entity_id", "")).strip()
        }

        def _normalize_string(text: str) -> str:
            """Normalize to alphanumeric only, lowercase."""
            text = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower()).strip()
            return re.sub(r"\s+", " ", text).strip()

        by_norm = {
            _normalize_string(c.get("entity_name")): c
            for c in canonical_candidates
            if c.get("entity_name")
        }

        verified: List[Dict[str, Any]] = []
        unmatched: List[Dict[str, Any]] = []

        for epic in extracted_epics:
            raw_name = str(epic.get("summary") or epic.get("key") or "").strip()
            raw_id = str(epic.get("key") or "").strip().lower()

            if not raw_name:
                unmatched.append(epic)
                continue

            candidate = None
            match_method = "none"

            # Strategy 1: Exact ID match
            if raw_id and raw_id in by_id:
                candidate = by_id[raw_id]
                match_method = "exact_id"

            # Strategy 2: Exact name match
            if candidate is None and raw_name.lower() in by_name:
                candidate = by_name[raw_name.lower()]
                match_method = "exact_name"

            # Strategy 3: Normalized string match
            if candidate is None:
                norm_raw = _normalize_string(raw_name)
                if norm_raw in by_norm:
                    candidate = by_norm[norm_raw]
                    match_method = "normalized"

            # Strategy 4: Fuzzy matching with SequenceMatcher
            if candidate is None:
                best_match = None
                best_score = 0.0
                norm_raw = _normalize_string(raw_name)

                for cand in canonical_candidates:
                    cand_name = str(cand.get("entity_name") or "").strip()
                    if not cand_name:
                        continue

                    norm_cand = _normalize_string(cand_name)
                    score = SequenceMatcher(None, norm_raw, norm_cand).ratio()

                    if score > best_score:
                        best_score = score
                        best_match = cand

                if best_match and best_score >= fuzzy_threshold:
                    candidate = best_match
                    match_method = f"fuzzy({best_score:.2f})"

            if candidate is None:
                logger.warning(
                    "[VERIFY EPIC] UNMATCHED: '%s' (key=%s)",
                    raw_name,
                    epic.get("key", "?"),
                )
                unmatched.append(epic)
                continue

            logger.info(
                "[VERIFY EPIC] MATCHED: '%s' -> '%s' via %s",
                raw_name,
                candidate.get("entity_name", ""),
                match_method,
            )

            verified.append({
                "extracted_epic": epic,
                "canonical_candidate": {
                    "entity_id": candidate.get("entity_id", ""),
                    "entity_name": candidate.get("entity_name", ""),
                    "description": candidate.get("description", ""),
                    "category": candidate.get("category", ""),
                },
                "match_method": match_method,
            })

        return {
            "verified": verified,
            "unmatched": unmatched,
            "summary": {
                "total_input": len(extracted_epics),
                "verified_count": len(verified),
                "unmatched_count": len(unmatched),
            }
        }

    @staticmethod
    def _extract_description_link_attachments(description: Any) -> List[Dict[str, Any]]:
        if not isinstance(description, str) or not description.strip():
            return []

        candidates: List[Tuple[str, str]] = []
        for pattern in (_DESC_WIKI_LINK_RE, _DESC_MD_LINK_RE):
            for match in pattern.finditer(description):
                label = str(match.group("label") or "").strip()
                url = str(match.group("url") or "").strip()
                if url:
                    candidates.append((label, url))

        for match in _DESC_BARE_FILE_URL_RE.finditer(description):
            url = str(match.group("url") or "").strip()
            if url:
                candidates.append(("", url))

        seen: set[str] = set()
        out: List[Dict[str, Any]] = []
        for label, url in candidates:
            url_key = url.lower()
            if url_key in seen:
                continue
            seen.add(url_key)

            filename = JiraValueStreamClient._infer_filename(label, url)
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            mime_type = _EXT_TO_MIME.get(ext, "application/octet-stream")
            synthetic_id = f"desc-link-{sha256(url.encode('utf-8')).hexdigest()[:12]}"

            out.append(
                {
                    "id": synthetic_id,
                    "filename": filename,
                    "mimeType": mime_type,
                    "content": url,
                    "size": 0,
                    "created": "",
                    "source": "description_link",
                    "is_description_link": True,
                    "display_text": label or filename,
                }
            )

        return out

    @staticmethod
    def _infer_filename(label: str, url: str) -> str:
        label = (label or "").strip()
        if "." in label and len(label.rsplit(".", 1)[-1]) <= 5:
            return label

        parsed = urlparse(url)
        basename = unquote(parsed.path.rsplit("/", 1)[-1]) if parsed.path else ""
        if basename:
            return basename

        return label or "description link attachment"

    # -------------------------------------------------------------------------
    # Attachment handling
    # -------------------------------------------------------------------------

    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> Any:
        """
        Download a single attachment.

        Supports two calling conventions:
        - download_attachment(url: str, dest_path: str) -> saves to disk
        - download_attachment(att: dict) -> returns raw bytes (pipeline compat)

        Reuses the authenticated httpx client created by authenticate() to
        avoid the overhead and auth inconsistency of spawning a new client
        per download.
        """
        if isinstance(url_or_att, dict):
            url = url_or_att.get("content", "")
        else:
            url = url_or_att

        if not url:
            raise ValueError("No download URL found in attachment")

        http_client = self.client._client
        if http_client is None:
            raise RuntimeError("Call authenticate() before downloading attachments.")

        response = await self._get_with_retry(http_client, url)
        response.raise_for_status()

        if dest_path:
            with open(dest_path, "wb") as f:
                f.write(response.content)
            return dest_path

        return response.content

    async def fetch_attachment_content(
        self,
        attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Download each attachment and extract its text content using MarkItDown.

        Returns a list of dicts with keys:
          - filename:    original attachment filename
          - mime_type:   MIME type reported by Jira
          - text_content: extracted Markdown text (empty string on failure)
          - error:       None | 'no_content_url' | 'markitdown_not_installed'
                         | '<ExcType>: <message>'

        Behavior when markitdown is unavailable:
          - Ingestion continues without raising
          - Each attachment gets error='markitdown_not_installed'

        Reuses the authenticated Jira session - no new clients created.
        """
        try:
            from markitdown import MarkItDown  # type: ignore
            md: Optional[Any] = MarkItDown()
        except ImportError:
            logger.warning("markitdown not installed; attachment text extraction disabled.")
            md = None

        http_client = self.client._client
        if http_client is None:
            raise RuntimeError("Call authenticate() before fetching attachment content.")

        results: List[Dict[str, Any]] = []
        for att in attachments:
            url = att.get("content", "")
            filename = att.get("filename", "")
            mime_type = att.get("mimeType", "")

            if not url:
                results.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "text_content": "",
                        "error": "no_content_url",
                    }
                )
                continue

            if md is None:
                results.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "text_content": "",
                        "error": "markitdown_not_installed",
                    }
                )
                continue

            try:
                response = await self._get_with_retry(http_client, url)
                response.raise_for_status()
                ext = f".{filename.rsplit('.', 1)[-1]}" if "." in filename else ""
                stream_info = self._build_stream_info(
                    mime_type=mime_type, ext=ext, filename=filename
                )

                raw_bytes = io.BytesIO(response.content)
                loop = asyncio.get_running_loop()

                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda b=raw_bytes, si=stream_info: md.convert_stream(b, stream_info=si),
                        ),
                        timeout=_ATTACHMENT_CONVERT_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Attachment conversion timed out after %.0fs for %s - skipping",
                        _ATTACHMENT_CONVERT_TIMEOUT_SEC,
                        filename,
                    )
                    results.append({
                        "filename": filename,
                        "mime_type": mime_type,
                        "text_content": "",
                        "error": "conversion_timeout",
                    })
                    continue

                results.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "text_content": result.text_content or "",
                    "error": None,
                })

            except Exception as exc:
                logger.exception("Attachment extraction failed for %s", filename)
                results.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "text_content": "",
                    "error": f"{type(exc).__name__}: {exc}",
                })

        return results

    async def _get_with_retry(
        self,
        http_client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response:
        """Download attachment bytes with bounded retries on transient failures."""
        delay = _ATTACHMENT_FETCH_BASE_DELAY_SEC
        last_exc: Exception | None = None

        for attempt in range(1, _ATTACHMENT_FETCH_RETRIES + 1):
            try:
                response = await http_client.get(url, timeout=120.0)
                if response.status_code in (408, 429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                return response

            except (
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.ConnectTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
                httpx.HTTPStatusError,
            ) as exc:
                last_exc = exc
                if attempt == _ATTACHMENT_FETCH_RETRIES:
                    break

                logger.warning(
                    "Attachment fetch retry %d/%d for %s (Xs) - waiting %.1fs",
                    attempt,
                    _ATTACHMENT_FETCH_RETRIES,
                    url,
                    type(exc).__name__,
                    delay,
                )

                await asyncio.sleep(delay)
                delay = min(delay * 2, 8.0)

        assert last_exc is not None
        raise last_exc

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _build_stream_info(mime_type: str, ext: str, filename: str) -> Any:
        """Build a MarkItDown StreamInfo for format detection. Returns None if unavailable."""
        try:
            from markitdown import StreamInfo  # type: ignore
        except ImportError:
            return None
        return StreamInfo(
            mimetype=mime_type or None,
            extension=ext or None,
            filename=filename or None,
        )