"""
Adapter: Jira REST ticket client.

Implements TicketFetcher using the Jira REST API via httpx.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ...ports.ticket_source import TicketFetcher
from .attachments.attachment_extraction import download_attachment, fetch_attachment_content
from .attachments.description_links import extract_description_link_attachments
from .client.rest import JIRARestClient as CoreJIRARestClient
from .value_stream.epic_extraction import extract_epics, map_value_streams_to_epics
from .value_stream.theme_extraction import extract_themes, resolve_value_streams

logger = logging.getLogger(__name__)


class JIRARestClient(CoreJIRARestClient):
    """High-level Jira REST helper built on the shared core client."""

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
        logger.info("Authenticated as %s", me.get("displayName", me.get("name")))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_issue_by_key(
        self,
        key: str,
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Call .authenticate() first.")
        url = f"{self.base_url}/rest/api/{self.jira_api_version}/issue/{key}"
        params: Dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()


class JiraTicketClient(TicketFetcher):
    """Async Jira client exposing ticket data and attachment extraction."""

    _BASE_FIELDS: List[str] = [
        "summary", "description", "reporter", "assignee",
        "created", "updated", "status", "priority", "issuetype",
        "labels", "components", "attachment", "issuelinks",
        "comment", "parent", "subtasks",
    ]

    def __init__(
        self,
        base_url: str,
        token: str,
        verify_ssl: bool = False,
        sharepoint_client: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url
        self.token = token
        self.verify_ssl = verify_ssl
        self.sharepoint_client = sharepoint_client
        self.client = JIRARestClient(
            base_url=base_url,
            auth_token=token,
            api_token=token,
            verify_ssl=verify_ssl,
        )

    async def authenticate(self) -> None:
        await self.client._authenticate()

    async def close(self) -> None:
        await self.client.close()

    async def __aenter__(self) -> "JiraTicketClient":
        await self.authenticate()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def _build_ticket_fields(self, config: Optional[Any] = None) -> List[str]:
        base = list(self._BASE_FIELDS)
        jira_field_map: dict = getattr(config, "jira_field_map", {}) if config else {}
        custom = sorted(
            v for v in jira_field_map.values()
            if isinstance(v, str) and v.startswith("customfield_")
        )
        return base + custom

    async def search_issues(
        self,
        jql: str,
        *,
        start_at: int = 0,
        max_results: int = 50,
        config: Optional[Any] = None,
    ) -> Dict[str, Any]:
        return await self.client.get_issues(
            jql=jql,
            start_at=start_at,
            max_results=max_results,
            fields=self._build_ticket_fields(config),
        )

    async def get_filter(self, filter_id: str) -> Dict[str, Any]:
        if self.client._client is None:
            raise RuntimeError("Call .authenticate() first.")
        url = f"{self.base_url}/rest/api/{self.client.jira_api_version}/filter/{filter_id}"
        response = await self.client._client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_ticket_data(
        self,
        ticket_id: str,
        config: Optional[Any] = None,
        llm_client: Optional[Any] = None,
    ) -> Dict[str, Any]:
        issue = await self.client.get_issue_by_key(
            ticket_id, fields=self._build_ticket_fields(config)
        )
        fields = issue.get("fields", {})

        attachments = fields.get("attachment", [])
        description_attachments = extract_description_link_attachments(fields.get("description"))
        merged_attachments = _merge_attachments(attachments, description_attachments)

        issuelinks = fields.get("issuelinks", [])
        themes = extract_themes(issuelinks)
        vs_data = resolve_value_streams(themes, issuelinks, llm_client=llm_client)

        epics = extract_epics(issue, config=config)
        value_stream_epics = map_value_streams_to_epics(vs_data["linked_value_streams"], epics)

        return {
            "key": issue.get("key", ticket_id),
            "fields": fields,
            "attachments": merged_attachments,
            "description_attachments": description_attachments,
            "themes": themes,
            "epics": epics,
            **vs_data,
            "value_stream_epics": value_stream_epics,
        }

    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> Any:
        if self.client._client is None:
            raise RuntimeError("Call authenticate() before downloading attachments.")
        return await download_attachment(
            self.client._client, url_or_att, dest_path,
            sharepoint_client=self.sharepoint_client,
        )

    async def fetch_attachment_content(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if self.client._client is None:
            raise RuntimeError("Call authenticate() before fetching attachment content.")
        return await fetch_attachment_content(
            self.client._client, attachments,
            sharepoint_client=self.sharepoint_client,
        )

    @staticmethod
    def _extract_description_link_attachments(description: Any) -> List[Dict[str, Any]]:
        return extract_description_link_attachments(description)


def _merge_attachments(
    api_attachments: List[Dict[str, Any]],
    description_attachments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    existing_keys: set[str] = set()
    for att in api_attachments:
        existing_keys.add(str(att.get("filename") or "").strip().lower())

    merged = list(api_attachments)
    for att in description_attachments:
        key = (
            str(att.get("content") or "").strip().lower(),
            str(att.get("filename") or "").strip().lower(),
        )
        if key[1] in existing_keys:
            continue
        merged.append(att)
        existing_keys.add(key[1])

    return merged
