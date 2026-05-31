"""Canonical Azure-ready IDMT document shape and schema."""

from __future__ import annotations

from vs_app.ingestion.index_documents.azure_idmt_schema import build_idmt_index_schema
from vs_app.ingestion.index_documents.idmt_document_builder import (
    build_document_from_stage_dataset_row,
    build_indexed_idmt_document,
)
from vs_app.ingestion.index_documents.models import (
    StageSupport,
    TicketContext,
    ValueStreamSupport,
)

__all__ = [
    "TicketContext",
    "ValueStreamSupport",
    "StageSupport",
    "build_indexed_idmt_document",
    "build_document_from_stage_dataset_row",
    "build_idmt_index_schema",
]
