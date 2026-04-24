"""Backward-compat shim. Canonical module: vs_app.container."""

from vs_app.container import (  # noqa: F401
    VALID_TICKET_SOURCES,
    TicketSourceFactory,
    build_ticket_fetcher,
    normalize_ticket_source,
)
