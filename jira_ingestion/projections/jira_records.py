"""Compatibility wrapper for Jira record projections."""

from jira_ingestion.projections.jira_record_projection import (
    build_chunk_record,
    build_valuestream_record,
    extract_retrieval_text,
    project_ticket_outputs,
)

__all__ = [
    "build_chunk_record",
    "build_valuestream_record",
    "extract_retrieval_text",
    "project_ticket_outputs",
]