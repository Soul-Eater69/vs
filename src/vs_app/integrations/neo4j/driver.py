"""Neo4j driver lifecycle helpers.

Thin wrappers around `neo4j.GraphDatabase.driver(...)` + verify_connectivity +
close, run on a worker thread so they're safe from an async context.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def create_driver(uri: str, auth: tuple[str, str]) -> Any:
    """Return a verified Neo4j driver. Raises RuntimeError if `neo4j` is not installed."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError(
            "The Neo4j ticket source requires the `neo4j` package. "
            "Install it with `pip install neo4j`."
        ) from exc

    driver = GraphDatabase.driver(uri, auth=auth)
    await asyncio.to_thread(driver.verify_connectivity)
    return driver


async def close_driver(driver: Any) -> None:
    if driver is None:
        return
    await asyncio.to_thread(driver.close)
