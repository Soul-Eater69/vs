"""Historic ground truth extraction and support classification.

Ground truth comes from Jira artifacts, not from prediction:

    Value Stream GT = linked Theme / GROUP issues
    Stage GT        = Epics under each Theme / GROUP (Epic title = stage)

This package will hold:

    value_stream_ground_truth  extract + validate linked Theme/GROUP issues
    value_stream_support       classify support for each Value Stream GT item
    stage_ground_truth         extract stage GT from Epics under each Theme
    stage_support              classify support for each stage GT item

Support is classified as one of:
``direct``, ``implied``, ``weak_broad``, ``not_in_context``, ``unknown``.

The name is ``ground_truth`` (never ``labels``): these are Jira answer-key
artifacts. It is created here as part of the ingestion framework structure
(Feature 2). The Value Stream / stage GT logic currently living under
``vs_app.ingestion.jira.value_stream_labels`` and ``vs_app.modules.stages`` is
moved in during the ground truth cleanups (Features 6-8). Until then this
package is intentionally empty and exports nothing.
"""

from __future__ import annotations

__all__: list[str] = []
