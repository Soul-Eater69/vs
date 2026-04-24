"""Shared ingestion mode aliases."""

from __future__ import annotations

from typing import Literal

IngestionMode = Literal["summary", "chunks", "both"]
TicketSourceName = Literal["jira", "neo4j"]

__all__ = ["IngestionMode", "TicketSourceName"]
