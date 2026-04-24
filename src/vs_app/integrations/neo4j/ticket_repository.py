"""Neo4j ticket Cypher execution.

Owns running the ticket + theme queries and returning raw node records. Does
no payload shaping; that belongs in `mappers`. Identifiers must already be
sanitized by the caller (see `cypher.tickets.sanitize_identifier`).
"""

from __future__ import annotations

import asyncio
from typing import Any

from .cypher.tickets import (
    DISCOVERY_THEMES_QUERY,
    DISCOVERY_TICKET_QUERY,
    GROUP_LINK_FIELD_CANDIDATES,
    ISSUE_TYPE_PROPERTY_CANDIDATES,
    KEY_PROPERTY_CANDIDATES,
    build_themes_query,
    build_ticket_query,
    candidate_property_names,
    normalize_for_match,
)
from .mappers import extract_group_keys, first_present_value


class Neo4jTicketRepository:
    """Executes Cypher to fetch a ticket node and its linked theme nodes."""

    def __init__(
        self,
        driver: Any,
        *,
        database: str,
        ticket_label: str,
        ticket_key_property: str,
        group_link_field: str,
        issue_type_property: str,
        theme_issue_type: str,
        group_key_prefix: str,
    ) -> None:
        self._driver = driver
        self.database = database
        # Caller is responsible for sanitizing the identifiers below — they're
        # interpolated into Cypher text.
        self.ticket_label = ticket_label
        self.ticket_key_property = ticket_key_property
        self.group_link_field = group_link_field
        self.issue_type_property = issue_type_property
        self.theme_issue_type = theme_issue_type
        self.group_key_prefix = group_key_prefix

    async def fetch_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        """Return {'ticket_node': dict, 'theme_nodes': list[dict]} or None if not found."""
        return await asyncio.to_thread(self._query_ticket_sync, ticket_id)

    # ------------------------------------------------------------------
    # Synchronous query execution (run on a worker thread)
    # ------------------------------------------------------------------

    def _query_ticket_sync(self, ticket_id: str) -> dict[str, Any] | None:
        with self._driver.session(database=self.database) as session:
            row = session.run(
                build_ticket_query(self.ticket_label, self.ticket_key_property),
                self._ticket_query_params(ticket_id),
            ).single()

        if row is None:
            with self._driver.session(database=self.database) as session:
                row = session.run(
                    DISCOVERY_TICKET_QUERY,
                    self._discovery_ticket_query_params(ticket_id),
                ).single()
            if row is None:
                return None

        ticket_node = row.get("ticket_node")
        if ticket_node is None:
            return None

        ticket_payload = dict(ticket_node)
        group_keys = extract_group_keys(
            first_present_value(
                ticket_payload, self.group_link_field, *GROUP_LINK_FIELD_CANDIDATES,
            ),
            group_key_prefix=self.group_key_prefix,
        )
        theme_nodes = self._query_theme_nodes_sync(group_keys) if group_keys else []
        return {"ticket_node": ticket_payload, "theme_nodes": theme_nodes}

    def _query_theme_nodes_sync(self, group_keys: list[str]) -> list[dict[str, Any]]:
        if not group_keys:
            return []

        with self._driver.session(database=self.database) as session:
            rows = list(session.run(
                build_themes_query(
                    self.ticket_label, self.ticket_key_property, self.issue_type_property,
                ),
                self._themes_query_params(group_keys),
            ))
        if not rows:
            with self._driver.session(database=self.database) as session:
                rows = list(session.run(
                    DISCOVERY_THEMES_QUERY, self._discovery_themes_query_params(group_keys),
                ))
        out = [
            dict(row["theme_node"])
            for row in rows
            if row.get("theme_node") is not None
        ]
        return sorted(out, key=lambda node: str(
            first_present_value(node, self.ticket_key_property, *KEY_PROPERTY_CANDIDATES) or ""
        ))

    # ------------------------------------------------------------------
    # Query parameter builders
    # ------------------------------------------------------------------

    def _ticket_query_params(self, ticket_id: str) -> dict[str, Any]:
        return {"ticket_key_upper": normalize_for_match(ticket_id)}

    def _discovery_ticket_query_params(self, ticket_id: str) -> dict[str, Any]:
        return {
            "ticket_key_upper": normalize_for_match(ticket_id),
            "key_properties": candidate_property_names(
                self.ticket_key_property, KEY_PROPERTY_CANDIDATES,
            ),
        }

    def _themes_query_params(self, group_keys: list[str]) -> dict[str, Any]:
        return {
            "group_keys_upper": [normalize_for_match(key) for key in group_keys],
            "theme_issue_type_upper": normalize_for_match(self.theme_issue_type),
        }

    def _discovery_themes_query_params(self, group_keys: list[str]) -> dict[str, Any]:
        return {
            "group_keys_upper": [normalize_for_match(key) for key in group_keys],
            "theme_issue_type_upper": normalize_for_match(self.theme_issue_type),
            "key_properties": candidate_property_names(
                self.ticket_key_property, KEY_PROPERTY_CANDIDATES,
            ),
            "issue_type_properties": candidate_property_names(
                self.issue_type_property, ISSUE_TYPE_PROPERTY_CANDIDATES,
            ),
        }
