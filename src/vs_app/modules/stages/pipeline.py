from __future__ import annotations

import logging
from typing import Any, Callable

from .catalog import get_stages_for_value_stream
from .finalizer import select_stages_with_llm

logger = logging.getLogger(__name__)

CatalogFn = Callable[..., list[dict]]
FinalizerFn = Callable[..., dict]


def predict_stages(
    *,
    condensed_idea_card: str,
    selected_value_streams: list[dict],
    index_name: str | None = None,
    catalog_fn: CatalogFn | None = None,
    finalizer_fn: FinalizerFn | None = None,
) -> dict:
    stage_predictions: list[dict] = []
    stage_candidate_debug: list[dict] = []
    if not selected_value_streams:
        return {
            "stage_predictions": [],
            "stage_candidate_debug": [],
        }

    catalog = catalog_fn or get_stages_for_value_stream
    finalizer = finalizer_fn or select_stages_with_llm

    for selected_vs in selected_value_streams:
        value_stream_id = str(selected_vs.get("entity_id") or "").strip()
        value_stream_name = str(selected_vs.get("entity_name") or "").strip()
        try:
            stages = catalog(
                value_stream_id=value_stream_id,
                value_stream_name=value_stream_name,
                index_name=index_name,
            )
        except Exception as exc:
            logger.exception("[STAGE] stage catalog lookup failed")
            stages = []
            catalog_error = f"{type(exc).__name__}: {exc}"
        else:
            catalog_error = ""

        stage_candidate_debug.append(
            {
                "value_stream_id": value_stream_id,
                "value_stream_name": value_stream_name,
                "candidate_stage_count": len(stages),
                "candidate_stages": stages,
                "catalog_error": catalog_error,
            }
        )

        if not stages:
            reason = catalog_error or "No stages found for selected value stream."
            stage_predictions.append(
                {
                    "value_stream_id": value_stream_id,
                    "value_stream_name": value_stream_name,
                    "stage_scope": "broad_or_unclear",
                    "selected_stages": [],
                    "reason": reason,
                }
            )
            continue

        prediction = finalizer(
            condensed_idea_card=condensed_idea_card,
            selected_value_stream=selected_vs,
            candidate_stages=stages,
        )
        stage_predictions.append(prediction)

    return {
        "stage_predictions": stage_predictions,
        "stage_candidate_debug": stage_candidate_debug,
    }
