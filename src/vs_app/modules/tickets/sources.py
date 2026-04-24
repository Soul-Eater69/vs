"""Ticket source port.

Any integration that can produce a NormalizedTicket (Jira REST, Neo4j graph,
mocks, JSON fixtures) implements this interface. Downstream ingestion code
depends only on this abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .models import NormalizedTicket


class TicketSource(ABC):
    async def __aenter__(self) -> "TicketSource":
        await self.authenticate()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def get_ticket(
        self,
        ticket_id: str,
        config: Any | None = None,
    ) -> NormalizedTicket: ...


class TicketFetcher(ABC):
    """Interface that any ticket data source must implement."""

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def get_ticket_data(
        self,
        ticket_id: str,
        config: Optional[Any] = None,
        llm_client: Optional[Any] = None,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def fetch_attachment_content(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    async def download_attachment(self, url_or_att: Any, dest_path: str = "") -> Any: ...


__all__ = ["TicketFetcher", "TicketSource"]
