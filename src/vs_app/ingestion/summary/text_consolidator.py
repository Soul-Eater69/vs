"""Compatibility shim for the relocated text consolidator.

The summary-mode text consolidation moved to
``vs_app.ingestion.extraction.text_consolidator`` in Feature 4. This module
re-exports the public API so existing imports keep working, e.g.::

    from vs_app.ingestion.summary.text_consolidator import consolidate_ticket_text

New code should import from ``vs_app.ingestion.extraction.text_consolidator``.
"""

from __future__ import annotations

from vs_app.ingestion.extraction.text_consolidator import (  # noqa: F401
    consolidate_ticket_text,
)

__all__ = ["consolidate_ticket_text"]
