"""Chunk document mapping helpers."""

from __future__ import annotations

from typing import Any, Optional


def build_chunk_provenance(
    *,
    source_type: str,
    attachment_id: str,
    attachment_name: str,
    attachment_type: str,
    chunk_id: str,
    chunk_index: int,
    page_num: Optional[int] = None,
    slide_num: Optional[int] = None,
    page_range: Optional[list[int]] = None,
    slide_range: Optional[list[int]] = None,
) -> dict[str, Any]:
    return {
        "chunkId": chunk_id,
        "chunkIndex": chunk_index,
        "sourceType": source_type,
        "attachmentId": attachment_id,
        "attachmentName": attachment_name,
        "attachmentType": attachment_type,
        "pageRange": page_range or ([page_num] if page_num is not None else []),
        "slideRange": slide_range or ([slide_num] if slide_num is not None else []),
    }


__all__ = ["build_chunk_provenance"]
