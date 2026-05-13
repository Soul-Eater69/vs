from __future__ import annotations

import json
import logging
from typing import Any

from vs_app import settings as config
from vs_app.modules.value_streams.canonical import normalize_vs_name

logger = logging.getLogger(__name__)

_STAGE_SELECT_FIELDS = ["entity_id", "entity_name", "properties", "content", "node_type"]


def get_stages_for_value_stream(
    value_stream_id: str | None,
    value_stream_name: str | None,
    *,
    index_name: str | None = None,
    client: Any | None = None,
) -> list[dict]:
    search_client = client or _make_client(index_name=index_name)
    row = _find_value_stream_row(search_client, value_stream_id, value_stream_name)
    if not row:
        return []
    return extract_stages_from_value_stream_row(row)


def extract_stages_from_value_stream_row(row: dict) -> list[dict]:
    properties = _parse_properties(row.get("properties"))
    raw_stages = properties.get("stages") if isinstance(properties, dict) else []
    if not isinstance(raw_stages, list):
        return []

    stages = [normalize_stage(raw) for raw in raw_stages if isinstance(raw, dict)]
    stages = [stage for stage in stages if stage.get("stage_id") and stage.get("stage_name")]
    return sorted(
        stages,
        key=lambda row: (
            row.get("stage_sequence") is None,
            int(row.get("stage_sequence") or 0),
            str(row.get("stage_name") or ""),
        ),
    )


def normalize_stage(raw: dict) -> dict:
    return {
        "stage_id": _text(raw.get("value_stream_stage_id") or raw.get("stage_id")),
        "stage_sequence": _to_int(
            raw.get("value_stream_stage_sequence")
            or raw.get("stage_sequence")
        ),
        "stage_name": _text(raw.get("value_stream_stage_name") or raw.get("stage_name")),
        "stage_display_name": _text(
            raw.get("value_stream_stage_display_name")
            or raw.get("stage_display_name")
        ),
        "stage_description": _text(
            raw.get("value_stream_stage_description")
            or raw.get("stage_description")
        ),
        "stage_entrance_criteria": _text(
            raw.get("value_stream_stage_entrance_criteria")
            or raw.get("stage_entrance_criteria")
        ),
        "stage_exit_criteria": _text(
            raw.get("value_stream_stage_exit_criteria")
            or raw.get("stage_exit_criteria")
        ),
        "stage_value_items": _text(
            raw.get("value_stream_stage_value_items")
            or raw.get("stage_value_items")
        ),
        "stage_stakeholders": _text(
            raw.get("value_stream_stage_stakeholders")
            or raw.get("stage_stakeholders")
        ),
    }


def _find_value_stream_row(
    client: Any,
    value_stream_id: str | None,
    value_stream_name: str | None,
) -> dict | None:
    entity_id = _text(value_stream_id)
    if entity_id:
        rows = _search_all(
            client,
            filter_expression=(
                "node_type eq 'ValueStream' and "
                f"entity_id eq '{_odata_quote(entity_id)}'"
            ),
        )
        if rows:
            return rows[0]

    name = _text(value_stream_name)
    if name:
        rows = _search_all(
            client,
            filter_expression=(
                "node_type eq 'ValueStream' and "
                f"entity_name eq '{_odata_quote(name)}'"
            ),
        )
        if rows:
            return rows[0]

        rows = _search_all(
            client,
            filter_expression="node_type eq 'ValueStream'",
        )
        target = normalize_vs_name(name)
        for row in rows:
            if normalize_vs_name(str(row.get("entity_name") or "")) == target:
                return row

    return None


def _search_all(client: Any, *, filter_expression: str) -> list[dict]:
    try:
        return list(
            client.search_all(
                search_text="*",
                filter_expression=filter_expression,
                select=_STAGE_SELECT_FIELDS,
            )
        )
    except TypeError:
        return list(client.search_all("*", filter_expression, _STAGE_SELECT_FIELDS))


def _parse_properties(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Could not parse value-stream properties JSON")
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _make_client(*, index_name: str | None = None) -> Any:
    from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient

    return AzureDirectSearchClient(
        index_name=index_name or config.VALUE_STREAM_AZURE_SEARCH_INDEX_NAME,
    )


def _odata_quote(value: str) -> str:
    return str(value or "").replace("'", "''")


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
