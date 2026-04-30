"""Normalized ticket domain models.

These types are the source-agnostic representation of a ticket. Jira payloads
map into these models at the integration boundary, so downstream ingestion / RAG
code never sees source-specific shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TicketAttachment:
    id: str
    filename: str
    content_url: str = ""
    mime_type: str = ""
    size: int = 0
    source: str = ""
    is_description_link: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TicketComment:
    id: str
    body: str
    created: str = ""
    author: str = ""


@dataclass(slots=True)
class LinkedTheme:
    key: str
    summary: str
    status: str = ""
    issue_type: str = ""


@dataclass(slots=True)
class ValueStreamLabel:
    id: str = ""
    name: str = ""
    source: str = ""  # jira_issuelinks, jira_theme_fallback, llm, ...
    confidence: float = 1.0


@dataclass(slots=True)
class NormalizedTicket:
    ticket_id: str
    title: str
    description: str = ""
    comments: list[TicketComment] = field(default_factory=list)
    attachments: list[TicketAttachment] = field(default_factory=list)
    description_attachments: list[TicketAttachment] = field(default_factory=list)
    linked_themes: list[LinkedTheme] = field(default_factory=list)
    value_stream_labels: list[ValueStreamLabel] = field(default_factory=list)
    epics: list[dict[str, Any]] = field(default_factory=list)
    raw_source: str = ""
    raw_payload: dict[str, Any] = field(default_factory=dict)
