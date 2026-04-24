"""Backward-compat shim. Canonical package: vs_app.integrations.neo4j."""

from vs_app.integrations.neo4j import Neo4jTicketClient  # noqa: F401

__all__ = ["Neo4jTicketClient"]
