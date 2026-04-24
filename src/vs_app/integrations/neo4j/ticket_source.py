"""Neo4j ticket source: the public class implementing TicketFetcher.

Owns the async lifecycle (driver + httpx client for attachment downloads) and
delegates Cypher execution to `Neo4jTicketRepository`, payload construction to
`mappers.build_ticket_payload`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ingestion.ports.ticket_source import TicketFetcher
from vs_app.integrations.files.attachment_extraction import (
    download_attachment as _download_attachment,
    fetch_attachment_content as _fetch_attachment_content,
)

from .cypher.tickets import (
    DEFAULT_GROUP_LINK_FIELD,
    DEFAULT_ISSUE_TYPE_PROPERTY,
    DEFAULT_TICKET_KEY_PROPERTY,
    DEFAULT_TICKET_LABEL,
    GROUP_KEY_PREFIX,
    THEME_ISSUE_TYPE,
    sanitize_identifier,
)
from .driver import close_driver, create_driver
from .mappers import build_ticket_payload
from .ticket_repository import Neo4jTicketRepository

logger = logging.getLogger(__name__)


class Neo4jTicketClient(TicketFetcher):
    """Read ticket payloads from Neo4j and expose Jira-like accessors."""

    def __init__(
        self,
        *,
        uri: str,
        auth: tuple[str, str],
        database: str = "neo4j",
        ticket_label: str = DEFAULT_TICKET_LABEL,
        ticket_key_property: str = DEFAULT_TICKET_KEY_PROPERTY,
        group_link_field: str = DEFAULT_GROUP_LINK_FIELD,
        issue_type_property: str = DEFAULT_ISSUE_TYPE_PROPERTY,
        attachment_auth_token: str = "",
        verify_ssl: bool = False,
        sharepoint_client: Optional[Any] = None,
        theme_issue_type: str = THEME_ISSUE_TYPE,
        group_key_prefix: str = GROUP_KEY_PREFIX,
    ) -> None:
        self.uri = uri
        self.auth = auth
        self.database = database
        # Sanitize identifiers now — they get interpolated into Cypher text.
        self.ticket_label = sanitize_identifier(ticket_label) or DEFAULT_TICKET_LABEL
        self.ticket_key_property = sanitize_identifier(ticket_key_property) or DEFAULT_TICKET_KEY_PROPERTY
        self.group_link_field = sanitize_identifier(group_link_field) or DEFAULT_GROUP_LINK_FIELD
        self.issue_type_property = sanitize_identifier(issue_type_property) or DEFAULT_ISSUE_TYPE_PROPERTY
        self.attachment_auth_token = attachment_auth_token
        self.verify_ssl = verify_ssl
        self.sharepoint_client = sharepoint_client
        self.theme_issue_type = theme_issue_type
        self.group_key_prefix = group_key_prefix

        self._driver: Any | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._repository: Neo4jTicketRepository | None = None

    async def authenticate(self) -> None:
        if self._driver is None:
            self._driver = await create_driver(self.uri, self.auth)
            self._repository = Neo4jTicketRepository(
                self._driver,
                database=self.database,
                ticket_label=self.ticket_label,
                ticket_key_property=self.ticket_key_property,
                group_link_field=self.group_link_field,
                issue_type_property=self.issue_type_property,
                theme_issue_type=self.theme_issue_type,
                group_key_prefix=self.group_key_prefix,
            )
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
        await close_driver(self._driver)
        self._driver = None
        self._repository = None

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
        query_sync = getattr(self, "_query_ticket_sync", None)
        if self._repository is None and callable(query_sync):
            record = query_sync(ticket_id)
        else:
            assert self._repository is not None
            record = await self._repository.fetch_ticket(ticket_id)
        if not record or not record.get("ticket_node"):
            raise KeyError(
                f"Ticket not found in Neo4j: {ticket_id}. "
                f"Checked configured schema (label={self.ticket_label}, "
                f"key_property={self.ticket_key_property}, "
                f"group_link_field={self.group_link_field}, "
                f"issue_type_property={self.issue_type_property})."
            )

        return build_ticket_payload(
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
