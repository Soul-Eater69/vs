"""Compatibility shim for the relocated stage ground truth builder.

The canonical stage ground truth extraction moved to
``vs_app.ingestion.ground_truth.stage_ground_truth`` in Feature 7. This module
re-exports its public API so existing imports keep working, e.g.::

    from vs_app.modules.stages.stage_ground_truth import build_ticket_stage_ground_truth

New code should import from ``vs_app.ingestion.ground_truth.stage_ground_truth``.
"""

from __future__ import annotations

from vs_app.ingestion.ground_truth.stage_ground_truth import *  # noqa: F401,F403
from vs_app.ingestion.ground_truth.stage_ground_truth import __all__  # noqa: F401
