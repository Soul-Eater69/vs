"""Shared ingestion mode aliases."""

from __future__ import annotations

from typing import Literal

IngestionMode = Literal["summary"]
TicketSourceName = Literal["jira"]

__all__ = ["IngestionMode", "TicketSourceName"]
