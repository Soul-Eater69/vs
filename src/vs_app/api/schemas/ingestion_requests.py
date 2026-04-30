from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class IngestTicketRequest(BaseModel):
    source: Literal["jira"] = "jira"
    mode: Literal["summary"] = "summary"
    force: bool = False
    persist_debug: bool = False
