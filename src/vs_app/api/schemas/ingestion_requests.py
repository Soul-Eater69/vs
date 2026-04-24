from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class IngestTicketRequest(BaseModel):
    source: Literal["jira", "neo4j"] = "jira"
    mode: Literal["summary", "chunks", "both"] = "both"
    force: bool = False
    persist_debug: bool = False
