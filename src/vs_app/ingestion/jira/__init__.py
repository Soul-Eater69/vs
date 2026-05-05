"""Jira-specific ingestion payload mapping."""

from .mapper import build_ticket_payload

__all__ = ["build_ticket_payload"]
