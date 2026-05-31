"""End-to-end IDMT ingestion pipeline.

This package will hold the orchestration that wires the ingestion flow:

    Jira fetchers
      -> text extraction / consolidation
      -> summary generation
      -> Value Stream ground truth + support
      -> stage ground truth + support
      -> canonical IDMT document builder
      -> Azure uploader

The orchestrator (``idmt_ingestion_pipeline``) is assembled once the underlying
stages are isolated in their own packages. It is created here as part of the
ingestion framework structure (Feature 2) and is intentionally empty for now;
it exports nothing until the pipeline is wired up.
"""

from __future__ import annotations

__all__: list[str] = []
