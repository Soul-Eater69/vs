"""Neo4j-backed ticket client implementing the TicketFetcher port.

Reads ticket payloads from a Neo4j graph and normalizes them into the same
shape returned by JiraTicketClient so the ingestion pipeline is source-agnostic.

Assumptions (from the Neo4j audit notebooks in this repo):
  - tickets live as ``(:JIRA {key: ...})`` nodes
  - linked Theme tickets are resolved from ``inwardIssues`` keys starting with ``GROUP-``
  - linked Theme summaries are the value-stream fallback signal

Node properties may be plain values or JSON/literal-encoded strings — this
client normalizes them transparently.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urlparse

import httpx

from ...ports.ticket_source import TicketFetcher
from ..jira.attachments.attachment_extraction import (
    download_attachment as _download_attachment,
    fetch_attachment_content as _fetch_attachment_content,
)
from ..jira.attachments.constants import EXT_TO_MIME
from ..jira.attachments.description_links import extract_description_link_attachments
from ..jira.value_stream.epic_extraction import extract_epics, map_value_streams_to_epics
from ..jira.value_stream.theme_extraction import resolve_value_streams

logger = logging.getLogger(__name__)

_THEME_ISSUE_TYPE = "Theme"
_GROUP_KEY_PREFIX = "GROUP-"
_DEFAULT_TICKET_LABEL = "JIRA"
_DEFAULT_TICKET_KEY_PROPERTY = "key"
_DEFAULT_GROUP_LINK_FIELD = "inwardIssues"
_DEFAULT_ISSUE_TYPE_PROPERTY = "issueType"
_KEY_PROPERTY_CANDIDATES = (
    "key", "ticketKey", "ticket_key", "issueKey", "issue_key", "jiraKey", "jira_key",
)
_GROUP_LINK_FIELD_CANDIDATES = (
    "inwardIssues", "inward_issues", "linkedGroupKeys", "linked_group_keys", "groupKeys", "group_keys",
)
_ISSUE_TYPE_PROPERTY_CANDIDATES = (
    "issueType", "issue_type", "type", "ticketType", "ticket_type",
)

_ATTACHMENT_PROPERTY_KEYS = (
    "attachment", "attachments", "attachmentMetaData", "attachmentsMetaData",
    "attachmentMetadata", "attachmentsMetadata", "files", "documents",
)
_COMMENT_PROPERTY_KEYS = ("comment", "comments", "commentMetaData", "commentsMetaData")
_ISSUELINK_PROPERTY_KEYS = ("issuelinks", "issueLinks", "issueLinkMetaData", "issueLinksMetaData")

_DISCOVERY_TICKET_QUERY = """
MATCH (t)
WHERE any(prop IN $key_properties WHERE toUpper(trim(toString(t[prop]))) = $ticket_key_upper)
RETURN t AS ticket_node
LIMIT 1
"""

_DISCOVERY_THEMES_QUERY = """
MATCH (g)
WHERE any(prop IN $key_properties WHERE toUpper(trim(toString(g[prop]))) IN $group_keys_upper)
  AND any(prop IN $issue_type_properties WHERE toUpper(trim(toString(g[prop]))) = $theme_issue_type_upper)
RETURN g AS theme_node
"""


class Neo4jTicketClient(TicketFetcher):
    """Read ticket payloads from Neo4j and expose Jira-like accessors."""

    def __init__(
        self,
        *,
        uri: str,
        auth: tuple[str, str],
        database: str = "neo4j",
        ticket_label: str = _DEFAULT_TICKET_LABEL,
        ticket_key_property: str = _DEFAULT_TICKET_KEY_PROPERTY,
        group_link_field: str = _DEFAULT_GROUP_LINK_FIELD,
        issue_type_property: str = _DEFAULT_ISSUE_TYPE_PROPERTY,
        attachment_auth_token: str = "",
        verify_ssl: bool = False,
        sharepoint_client: Optional[Any] = None,
        theme_issue_type: str = _THEME_ISSUE_TYPE,
        group_key_prefix: str = _GROUP_KEY_PREFIX,
    ) -> None:
        self.uri = uri
        self.auth = auth
        self.database = database
        self.ticket_label = _sanitize_identifier(ticket_label) or _DEFAULT_TICKET_LABEL
        self.ticket_key_property = _sanitize_identifier(ticket_key_property) or _DEFAULT_TICKET_KEY_PROPERTY
        self.group_link_field = _sanitize_identifier(group_link_field) or _DEFAULT_GROUP_LINK_FIELD
        self.issue_type_property = _sanitize_identifier(issue_type_property) or _DEFAULT_ISSUE_TYPE_PROPERTY
        self.attachment_auth_token = attachment_auth_token
        self.verify_ssl = verify_ssl
        self.sharepoint_client = sharepoint_client
        self.theme_issue_type = theme_issue_type
        self.group_key_prefix = group_key_prefix

        self._driver: Any | None = None
        self._http_client: httpx.AsyncClient | None = None

    async def authenticate(self) -> None:
        if self._driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise RuntimeError(
                    "The Neo4j ticket source requires the `neo4j` package. "
                    "Install it with `pip install neo4j`."
                ) from exc

            self._driver = GraphDatabase.driver(self.uri, auth=self.auth)
            await asyncio.to_thread(self._driver.verify_connectivity)

        if self._http_client is None:
            headers = {"Accept": "application/json"}
            if self.attachment_auth_token:
                headers["Authorization"] = f"Bearer {self.attachment_auth_token}"
            self._http_client = httpx.AsyncClient(
                verify=self.verify_ssl, headers=headers, timeout=60.0,
            )

    async def close(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None
        if self._driver is not None:
            await asyncio.to_thread(self._driver.close)
        self._driver = None

    async def __aenter__(self) -> "Neo4jTicketClient":
        await self.authenticate()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def get_ticket_data(
        self,
        ticket_id: str,
        config: Optional[Any] = None,
        llm_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        await self._ensure_ready()
        record = await asyncio.to_thread(self._query_ticket_sync, ticket_id)
        if not record or not record.get("ticket_node"):
            raise KeyError(
                f"Ticket not found in Neo4j: {ticket_id}. "
                f"Checked configured schema (label={self.ticket_label}, "
                f"key_property={self.ticket_key_property}, "
                f"group_link_field={self.group_link_field}, "
                f"issue_type_property={self.issue_type_property})."
            )

        return _build_ticket_payload(
            ticket_id=ticket_id,
            ticket_node=record["ticket_node"],
            theme_nodes=record.get("theme_nodes") or [],
            config=config,
            llm_client=llm_client,
        )

    async def fetch_attachment_content(
        self, attachments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        await self._ensure_ready()
        assert self._http_client is not None
        return await _fetch_attachment_content(
            self._http_client, attachments, sharepoint_client=self.sharepoint_client,
        )

    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> Any:
        await self._ensure_ready()
        assert self._http_client is not None
        return await _download_attachment(
            self._http_client, url_or_att, dest_path=dest_path,
            sharepoint_client=self.sharepoint_client,
        )

    async def _ensure_ready(self) -> None:
        if self._driver is None or self._http_client is None:
            await self.authenticate()

    def _query_ticket_sync(self, ticket_id: str) -> dict[str, Any] | None:
        assert self._driver is not None

        with self._driver.session(database=self.database) as session:
            row = session.run(self._build_ticket_query(), self._ticket_query_params(ticket_id)).single()

        if row is None:
            with self._driver.session(database=self.database) as session:
                row = session.run(
                    _DISCOVERY_TICKET_QUERY,
                    self._discovery_ticket_query_params(ticket_id),
                ).single()
            if row is None:
                return None

        ticket_node = row.get("ticket_node")
        if ticket_node is None:
            return None

        ticket_payload = dict(ticket_node)
        group_keys = _extract_group_keys(
            _first_present_value(ticket_payload, self.group_link_field, *_GROUP_LINK_FIELD_CANDIDATES),
            group_key_prefix=self.group_key_prefix,
        )
        theme_nodes = self._query_theme_nodes_sync(group_keys) if group_keys else []
        return {"ticket_node": ticket_payload, "theme_nodes": theme_nodes}

    def _query_theme_nodes_sync(self, group_keys: list[str]) -> list[dict[str, Any]]:
        assert self._driver is not None
        if not group_keys:
            return []

        with self._driver.session(database=self.database) as session:
            rows = list(session.run(self._build_themes_query(), self._themes_query_params(group_keys)))
        if not rows:
            with self._driver.session(database=self.database) as session:
                rows = list(session.run(
                    _DISCOVERY_THEMES_QUERY, self._discovery_themes_query_params(group_keys),
                ))
        out = [
            dict(row["theme_node"])
            for row in rows
            if row.get("theme_node") is not None
        ]
        return sorted(out, key=lambda node: str(
            _first_present_value(node, self.ticket_key_property, *_KEY_PROPERTY_CANDIDATES) or ""
        ))

    def _build_ticket_query(self) -> str:
        return f"""
MATCH (t:{self.ticket_label})
WHERE toUpper(trim(toString(t.{self.ticket_key_property}))) = $ticket_key_upper
RETURN t AS ticket_node
LIMIT 1
"""

    def _ticket_query_params(self, ticket_id: str) -> dict[str, Any]:
        return {"ticket_key_upper": _normalize_for_match(ticket_id)}

    def _discovery_ticket_query_params(self, ticket_id: str) -> dict[str, Any]:
        return {
            "ticket_key_upper": _normalize_for_match(ticket_id),
            "key_properties": _candidate_property_names(self.ticket_key_property, _KEY_PROPERTY_CANDIDATES),
        }

    def _build_themes_query(self) -> str:
        return f"""
MATCH (g:{self.ticket_label})
WHERE toUpper(trim(toString(g.{self.ticket_key_property}))) IN $group_keys_upper
  AND toUpper(trim(toString(g.{self.issue_type_property}))) = $theme_issue_type_upper
RETURN g AS theme_node
"""

    def _themes_query_params(self, group_keys: list[str]) -> dict[str, Any]:
        return {
            "group_keys_upper": [_normalize_for_match(key) for key in group_keys],
            "theme_issue_type_upper": _normalize_for_match(self.theme_issue_type),
        }

    def _discovery_themes_query_params(self, group_keys: list[str]) -> dict[str, Any]:
        return {
            "group_keys_upper": [_normalize_for_match(key) for key in group_keys],
            "theme_issue_type_upper": _normalize_for_match(self.theme_issue_type),
            "key_properties": _candidate_property_names(self.ticket_key_property, _KEY_PROPERTY_CANDIDATES),
            "issue_type_properties": _candidate_property_names(
                self.issue_type_property, _ISSUE_TYPE_PROPERTY_CANDIDATES,
            ),
        }


def _build_ticket_payload(
    *,
    ticket_id: str,
    ticket_node: dict[str, Any],
    theme_nodes: list[dict[str, Any]],
    config: Any = None,
    llm_client: Any | None = None,
) -> dict[str, Any]:
    node = {key: _normalize_graph_value(value) for key, value in (ticket_node or {}).items()}

    fields = dict(node)
    fields["summary"] = str(node.get("summary") or _first_present_value(node, "title", "name") or ticket_id)
    fields["description"] = node.get("description") or ""
    fields["comment"] = _normalize_comment_container(node)
    fields["attachment"] = _normalize_attachments(node)
    fields["issuelinks"] = _normalize_issue_links(node)
    fields["labels"] = _normalize_string_list(node.get("labels"))
    fields["components"] = _normalize_named_list(node.get("components"))
    fields["reporter"] = _normalize_person(node.get("reporter"))
    fields["assignee"] = _normalize_person(node.get("assignee"))
    fields["created"] = str(node.get("created") or node.get("creationDate") or "")
    fields["updated"] = str(node.get("updated") or node.get("lastUpdated") or "")
    fields["status"] = _normalize_named_object(node.get("status"))
    fields["priority"] = _normalize_named_object(node.get("priority"))
    fields["issuetype"] = _normalize_named_object(
        _first_present_value(node, "issuetype", "issueType", "issue_type", "type", "ticketType")
    )

    parent = _normalize_issue_ref(node.get("parent"))
    if parent:
        fields["parent"] = parent
    subtasks = _normalize_issue_refs(node.get("subtasks"))
    if subtasks:
        fields["subtasks"] = subtasks

    description_attachments = extract_description_link_attachments(fields.get("description"))
    attachments = _merge_attachments(fields["attachment"], description_attachments)
    themes = _normalize_theme_nodes(theme_nodes)
    vs_data = resolve_value_streams(themes, fields["issuelinks"], llm_client=llm_client)
    epics = extract_epics({"fields": fields}, config=config)
    value_stream_epics = map_value_streams_to_epics(vs_data.get("linked_value_streams") or [], epics)

    return {
        "key": str(node.get("key") or ticket_id),
        "fields": fields,
        "attachments": attachments,
        "description_attachments": description_attachments,
        "themes": themes,
        "epics": epics,
        **vs_data,
        "value_stream_epics": value_stream_epics,
    }


def _normalize_graph_value(value: Any) -> Any:
    parsed = _parse_jsonish(value)
    if isinstance(parsed, dict):
        return {str(key): _normalize_graph_value(val) for key, val in parsed.items()}
    if isinstance(parsed, (list, tuple)):
        return [_normalize_graph_value(item) for item in parsed]
    if isinstance(parsed, (str, int, float, bool)) or parsed is None:
        return parsed
    return str(parsed)


def _parse_jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if (text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}")):
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(text)
            except Exception:
                continue
    return value


def _normalize_for_match(value: Any) -> str:
    return str(value or "").strip().upper()


def _sanitize_identifier(value: str) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text) else ""


def _candidate_property_names(primary: str, fallbacks: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for name in (primary, *fallbacks):
        cleaned = _sanitize_identifier(name)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names


def _first_present_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            value = mapping.get(key)
            if value is not None:
                return value
    return None


def _normalize_string_list(value: Any) -> list[str]:
    value = _normalize_graph_value(value)
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    out: list[str] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            for key in ("name", "value", "displayName", "key"):
                candidate = str(item.get(key) or "").strip()
                if candidate:
                    out.append(candidate)
                    break
        else:
            candidate = str(item or "").strip()
            if candidate:
                out.append(candidate)
    return _unique_keep_order(out)


def _normalize_named_list(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in _normalize_string_list(value):
        out.append({"name": item})
    value = _normalize_graph_value(value)
    if isinstance(value, list):
        named: list[dict[str, str]] = []
        for item in value:
            if isinstance(item, dict):
                normalized = _normalize_named_object(item)
                if normalized:
                    named.append(normalized)
        if named:
            return named
    return out


def _normalize_named_object(value: Any) -> dict[str, str]:
    value = _normalize_graph_value(value)
    if isinstance(value, dict):
        for key in ("name", "value", "displayName", "status", "label"):
            candidate = str(value.get(key) or "").strip()
            if candidate:
                return {"name": candidate}
        if value:
            first = next(iter(value.values()))
            candidate = str(first or "").strip()
            if candidate:
                return {"name": candidate}
        return {}
    candidate = str(value or "").strip()
    return {"name": candidate} if candidate else {}


def _normalize_person(value: Any) -> dict[str, str]:
    value = _normalize_graph_value(value)
    if isinstance(value, dict):
        display = str(
            value.get("displayName") or value.get("name") or value.get("emailAddress") or ""
        ).strip()
        return {"displayName": display, "name": display} if display else {}
    candidate = str(value or "").strip()
    return {"displayName": candidate, "name": candidate} if candidate else {}


def _normalize_issue_links(node: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _ISSUELINK_PROPERTY_KEYS:
        raw_value = _normalize_graph_value(node.get(key))
        if not raw_value:
            continue
        if isinstance(raw_value, list):
            links = [item for item in raw_value if isinstance(item, dict)]
            if links:
                return links
    return []


def _normalize_comment_container(node: dict[str, Any]) -> dict[str, Any]:
    comments: list[dict[str, Any]] = []
    seen_bodies: set[str] = set()
    for key in _COMMENT_PROPERTY_KEYS:
        raw_value = _normalize_graph_value(node.get(key))
        for item in _collect_comment_items(raw_value, field_name=key):
            body = str(item.get("body") or "").strip()
            if not body:
                continue
            dedupe_key = body.lower()
            if dedupe_key in seen_bodies:
                continue
            seen_bodies.add(dedupe_key)
            comments.append(item)
    return {"comments": comments, "total": len(comments)}


def _collect_comment_items(value: Any, *, field_name: str, start_index: int = 1) -> list[dict[str, Any]]:
    value = _normalize_graph_value(value)
    if not value:
        return []
    if isinstance(value, dict):
        for wrapper_key in ("comments", "items", "values", "entries"):
            wrapped = value.get(wrapper_key)
            if isinstance(wrapped, list):
                items: list[dict[str, Any]] = []
                for idx, item in enumerate(wrapped, start=start_index):
                    items.extend(_collect_comment_items(item, field_name=field_name, start_index=idx))
                return items
        body = str(
            value.get("body") or value.get("comment") or value.get("text") or value.get("value") or ""
        ).strip()
        if not body:
            return []
        author = _normalize_person(value.get("author") or value.get("createdBy") or value.get("user"))
        return [{
            "id": str(value.get("id") or f"{field_name}-{start_index}"),
            "body": body,
            "created": str(value.get("created") or value.get("createdAt") or ""),
            "author": author,
        }]
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for idx, item in enumerate(value, start=start_index):
            items.extend(_collect_comment_items(item, field_name=field_name, start_index=idx))
        return items
    body = str(value).strip()
    if not body:
        return []
    return [{"id": f"{field_name}-{start_index}", "body": body, "created": "", "author": {}}]


def _normalize_attachments(node: dict[str, Any]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for key in _ATTACHMENT_PROPERTY_KEYS:
        raw_value = _normalize_graph_value(node.get(key))
        for att in _collect_attachment_items(raw_value, source_key=key):
            dedupe_key = (
                str(att.get("content") or "").strip().lower(),
                str(att.get("filename") or "").strip().lower(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            attachments.append(att)
    return attachments


def _collect_attachment_items(value: Any, *, source_key: str) -> list[dict[str, Any]]:
    value = _normalize_graph_value(value)
    if not value:
        return []
    if isinstance(value, dict):
        for wrapper_key in ("attachments", "items", "values", "results"):
            wrapped = value.get(wrapper_key)
            if isinstance(wrapped, list):
                items: list[dict[str, Any]] = []
                for item in wrapped:
                    items.extend(_collect_attachment_items(item, source_key=source_key))
                return items
        attachment = _normalize_attachment(value, source_key=source_key)
        return [attachment] if attachment else []
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            items.extend(_collect_attachment_items(item, source_key=source_key))
        return items
    attachment = _normalize_attachment(value, source_key=source_key)
    return [attachment] if attachment else []


def _normalize_attachment(value: Any, *, source_key: str) -> dict[str, Any] | None:
    value = _normalize_graph_value(value)
    if value is None:
        return None
    if isinstance(value, dict):
        filename = _first_string(value, "filename", "name", "title", "label", "displayName")
        content = _first_string(value, "content", "url", "downloadUrl", "downloadURL", "href")
        if not filename and content:
            filename = _infer_filename_from_url(content)
        if not filename and not content:
            return None
        ext = _extension_of(filename)
        mime_type = _first_string(value, "mimeType", "mime_type") or EXT_TO_MIME.get(ext, "application/octet-stream")
        return {
            "id": str(value.get("id") or value.get("attachment_id") or filename or content),
            "filename": filename or "attachment",
            "mimeType": mime_type,
            "content": content,
            "size": _int_or_zero(value.get("size")),
            "created": str(value.get("created") or value.get("createdAt") or ""),
            "source": str(value.get("source") or source_key or "neo4j"),
            "is_description_link": bool(value.get("is_description_link")),
        }
    raw = str(value).strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        filename = _infer_filename_from_url(raw)
        ext = _extension_of(filename)
        return {
            "id": raw, "filename": filename or "attachment",
            "mimeType": EXT_TO_MIME.get(ext, "application/octet-stream"),
            "content": raw, "size": 0, "created": "", "source": source_key or "neo4j",
            "is_description_link": False,
        }
    ext = _extension_of(raw)
    return {
        "id": raw, "filename": raw,
        "mimeType": EXT_TO_MIME.get(ext, "application/octet-stream"),
        "content": "", "size": 0, "created": "", "source": source_key or "neo4j",
        "is_description_link": False,
    }


def _normalize_theme_nodes(theme_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for node in theme_nodes or []:
        normalized = {key: _normalize_graph_value(value) for key, value in node.items()}
        key = str(normalized.get("key") or "").strip()
        summary_raw = str(normalized.get("summary") or "").strip()
        dedupe_key = (key, summary_raw)
        if not summary_raw or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        themes.append({
            "key": key,
            "summary": summary_raw,
            "summary_raw": summary_raw,
            "status": str(normalized.get("status") or "").strip(),
            "issue_type": str(normalized.get("issueType") or normalized.get("issuetype") or "").strip(),
        })
    return themes


def _extract_group_keys(value: Any, *, group_key_prefix: str) -> list[str]:
    prefix = str(group_key_prefix or "").strip().upper()
    keys = []
    for item in _normalize_string_list(value):
        candidate = str(item or "").strip()
        if not candidate:
            continue
        if prefix and not candidate.upper().startswith(prefix):
            continue
        keys.append(candidate)
    return _unique_keep_order(keys)


def _normalize_issue_ref(value: Any) -> dict[str, Any] | None:
    value = _normalize_graph_value(value)
    if not value:
        return None
    if isinstance(value, dict):
        key = str(value.get("key") or value.get("id") or "").strip()
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
        if key or fields:
            return {"key": key, "fields": fields}
        return None
    key = str(value).strip()
    return {"key": key, "fields": {}} if key else None


def _normalize_issue_refs(value: Any) -> list[dict[str, Any]]:
    value = _normalize_graph_value(value)
    if not value:
        return []
    if isinstance(value, list):
        refs = [_normalize_issue_ref(item) for item in value]
        return [ref for ref in refs if ref]
    ref = _normalize_issue_ref(value)
    return [ref] if ref else []


def _merge_attachments(
    api_attachments: list[dict[str, Any]],
    description_attachments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing: set[str] = set()
    merged = list(api_attachments)
    for attachment in api_attachments:
        existing.add(str(attachment.get("filename") or "").strip().lower())
    for attachment in description_attachments:
        filename_key = str(attachment.get("filename") or "").strip().lower()
        if filename_key and filename_key in existing:
            continue
        merged.append(attachment)
        if filename_key:
            existing.add(filename_key)
    return merged


def _first_string(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        candidate = str(value or "").strip()
        if candidate:
            return candidate
    return ""


def _extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _infer_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    basename = unquote(parsed.path.rsplit("/", 1)[-1]) if parsed.path else ""
    return basename or "attachment"


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _unique_keep_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out
