from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from vs_app import settings as config

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_JSON_CATALOG = _REPO_ROOT / "data" / "value_stream_stage_catalog.json"
_VALUE_STREAM_SELECT_FIELDS = [
    "entity_id",
    "entity_name",
    "content",
    "properties",
    "node_type",
]


def load_stage_catalog(
    path: str | Path | None = None,
    source: str = "json",
    *,
    client: Any | None = None,
    index_name: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Load approved value-stream stages from JSON or Azure AI Search."""
    source_key = str(source or "json").strip().lower()
    if source_key == "azure":
        return _load_catalog_from_azure(client=client, index_name=index_name)
    if source_key != "json":
        raise ValueError(f"Unsupported stage catalog source: {source}")
    return _load_catalog_from_json(Path(path) if path else _DEFAULT_JSON_CATALOG)


def get_allowed_stages(value_stream_name: str, catalog: dict) -> list[str]:
    entry = get_value_stream_catalog_entry(value_stream_name, catalog)
    if not entry:
        return []
    return [
        str(stage.get("name") or "").strip()
        for stage in entry.get("stages") or []
        if str(stage.get("name") or "").strip()
    ]


def get_value_stream_catalog_entry(
    value_stream_name: str,
    catalog: dict,
) -> dict[str, Any] | None:
    target = normalize_catalog_name(value_stream_name)
    if not target:
        return None

    for name, entry in (catalog or {}).items():
        if normalize_catalog_name(name) == target:
            return entry if isinstance(entry, dict) else _normalize_catalog_entry(name, entry)

        if isinstance(entry, dict):
            aliases = entry.get("aliases") or entry.get("value_stream_aliases") or []
            if any(normalize_catalog_name(alias) == target for alias in aliases):
                return entry
            entry_name = entry.get("name") or entry.get("value_stream_name")
            if entry_name and normalize_catalog_name(entry_name) == target:
                return entry

    return None


def normalize_catalog_name(value: Any) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _load_catalog_from_json(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Stage catalog JSON not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Stage catalog JSON must be an object keyed by value stream name")

    catalog: dict[str, dict[str, Any]] = {}
    for value_stream_name, raw_entry in payload.items():
        entry = _normalize_catalog_entry(str(value_stream_name), raw_entry)
        if entry["stages"]:
            catalog[str(value_stream_name)] = entry
    return catalog


def _load_catalog_from_azure(
    *,
    client: Any | None,
    index_name: str | None,
) -> dict[str, dict[str, Any]]:
    search_client = client or _make_azure_client(index_name=index_name)
    rows = _search_value_stream_rows(search_client)
    catalog: dict[str, dict[str, Any]] = {}

    for row in rows:
        value_stream_name = _text(row.get("entity_name"))
        if not value_stream_name:
            continue
        properties = _parse_json_object(row.get("properties"))
        raw_entry = {
            "value_stream_id": _text(row.get("entity_id")),
            "stages": _extract_stage_defs(properties, row.get("content")),
        }
        entry = _normalize_catalog_entry(value_stream_name, raw_entry)
        if entry["stages"]:
            catalog[value_stream_name] = entry

    return catalog


def _normalize_catalog_entry(value_stream_name: str, raw_entry: Any) -> dict[str, Any]:
    if isinstance(raw_entry, list):
        raw_stages = raw_entry
        value_stream_id = ""
        aliases: list[str] = []
    elif isinstance(raw_entry, dict):
        value_stream_id = _text(
            raw_entry.get("value_stream_id")
            or raw_entry.get("id")
            or raw_entry.get("entity_id")
        )
        aliases = _list_text(raw_entry.get("aliases") or raw_entry.get("value_stream_aliases"))
        raw_stages = _extract_stage_defs(raw_entry, raw_entry.get("content"))
    else:
        raw_stages = []
        value_stream_id = ""
        aliases = []

    stages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_stage in raw_stages:
        stage = _normalize_stage_def(raw_stage)
        key = normalize_catalog_name(stage.get("name"))
        if not key or key in seen:
            continue
        seen.add(key)
        stages.append(stage)

    return {
        "value_stream_id": value_stream_id,
        "stages": stages,
        "aliases": aliases,
    }


def _extract_stage_defs(raw: Any, content: Any = None) -> list[Any]:
    if not isinstance(raw, dict):
        return []

    for key in ("stages", "value_stages", "stage_names"):
        value = raw.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, str) and value.strip():
            parsed = _parse_json_any(value)
            if isinstance(parsed, list):
                return parsed

    stages_json = raw.get("stages_json")
    if isinstance(stages_json, str) and stages_json.strip():
        parsed = _parse_json_any(stages_json)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return _extract_stage_defs(parsed)

    if content:
        return _extract_stages_from_content(str(content))

    return []


def _normalize_stage_def(raw_stage: Any) -> dict[str, Any]:
    if isinstance(raw_stage, str):
        return {"name": _text(raw_stage), "id": "", "description": "", "aliases": []}

    if not isinstance(raw_stage, dict):
        return {"name": "", "id": "", "description": "", "aliases": []}

    name = _text(
        raw_stage.get("name")
        or raw_stage.get("stage_name")
        or raw_stage.get("value_stream_stage_name")
        or raw_stage.get("stage_display_name")
        or raw_stage.get("value_stream_stage_display_name")
    )
    stage_id = _text(
        raw_stage.get("id")
        or raw_stage.get("stage_id")
        or raw_stage.get("value_stream_stage_id")
    )
    description = _text(
        raw_stage.get("description")
        or raw_stage.get("stage_description")
        or raw_stage.get("value_stream_stage_description")
    )
    aliases = _list_text(raw_stage.get("aliases") or raw_stage.get("stage_aliases"))

    return {
        "name": name,
        "id": stage_id,
        "description": description,
        "aliases": aliases,
    }


def _extract_stages_from_content(content: str) -> list[str]:
    stages: list[str] = []
    for line in str(content or "").splitlines():
        text = line.strip(" -*\t")
        if not text:
            continue
        match = re.search(r"\b(?:stage|value stage)\s*[:\-]\s*(.+)$", text, re.I)
        if match:
            text = match.group(1).strip()
        elif not re.match(r"^\d+[\).]\s+", text):
            continue
        text = re.sub(r"^\d+[\).]\s+", "", text).strip()
        text = text.split("|", 1)[0].strip()
        if text:
            stages.append(text)
    return stages


def _search_value_stream_rows(client: Any) -> list[dict[str, Any]]:
    try:
        return list(
            client.search_all(
                search_text="*",
                filter_expression="node_type eq 'ValueStream'",
                select=_VALUE_STREAM_SELECT_FIELDS,
            )
        )
    except TypeError:
        return list(
            client.search_all(
                "*",
                "node_type eq 'ValueStream'",
                _VALUE_STREAM_SELECT_FIELDS,
            )
        )


def _make_azure_client(*, index_name: str | None) -> Any:
    from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient

    return AzureDirectSearchClient(
        index_name=index_name or config.VALUE_STREAM_AZURE_SEARCH_INDEX_NAME,
    )


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    parsed = _parse_json_any(value)
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_any(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.debug("Could not parse stage catalog JSON fragment")
        return None


def _list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "get_allowed_stages",
    "get_value_stream_catalog_entry",
    "load_stage_catalog",
    "normalize_catalog_name",
]
