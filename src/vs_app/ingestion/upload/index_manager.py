"""Index manager for the Theme-generation POC Azure index.

Converts the plain-dict Feature 9 schema into an Azure SDK ``SearchIndex`` and
creates/recreates it behind strict safety gates. Recreate/delete requires all of:
  1. an explicit ``recreate=True`` call,
  2. ``THEME_GEN_ALLOW_RECREATE=1`` in the environment,
  3. a name guard that only ever allows the configured theme-generation POC index
     (and never a protected value-stream/historical/summary index).

The SDK index client is constructed lazily, so building the index definition and
the name/guard checks are testable without any Azure calls.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from vs_app.ingestion.index_documents.theme_generation_schema import (
    build_theme_generation_index_schema,
)
from vs_app.ingestion.upload.azure_search_client import (
    PROTECTED_INDEX_NAMES,
    embedding_dimensions,
    make_index_client,
    resolve_index_name,
)

logger = logging.getLogger(__name__)

_RECREATE_ENV = "THEME_GEN_ALLOW_RECREATE"
_TRUTHY = ("1", "true", "yes", "on")


def assert_safe_theme_generation_index_name(index_name: str) -> str:
    """Refuse any index that is not the configured theme-generation POC index."""
    name = (index_name or "").strip()
    configured = resolve_index_name()
    if not name:
        raise ValueError("theme-generation index name is empty")
    if name in PROTECTED_INDEX_NAMES:
        raise ValueError(
            f"refusing to manage protected index '{name}' "
            "(value-stream/historical/summary)"
        )
    if name != configured:
        raise ValueError(
            f"index '{name}' is not the configured theme-generation POC index "
            f"'{configured}'"
        )
    return name


def recreate_allowed() -> bool:
    return os.getenv(_RECREATE_ENV, "").strip().lower() in _TRUTHY


def build_search_index(
    *, index_name: str | None = None, dimensions: int | None = None
) -> Any:
    """Build the Azure SDK SearchIndex for the theme-generation POC schema."""
    name = resolve_index_name(index_name)
    dims = dimensions if dimensions is not None else embedding_dimensions()
    schema = build_theme_generation_index_schema(index_name=name, embedding_dimensions=dims)
    return search_index_from_schema(schema)


def search_index_from_schema(schema: dict[str, Any]) -> Any:
    """Convert the plain-dict schema into an Azure SDK ``SearchIndex``."""
    from azure.search.documents.indexes.models import (
        HnswAlgorithmConfiguration,
        SearchIndex,
        VectorSearch,
        VectorSearchProfile,
    )

    fields = [_to_search_field(field) for field in schema["fields"]]

    vector_cfg = schema.get("vectorSearch") or {}
    vector_search = None
    if vector_cfg:
        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(name=algo["name"])
                for algo in vector_cfg.get("algorithms", [])
            ],
            profiles=[
                VectorSearchProfile(
                    name=profile["name"],
                    algorithm_configuration_name=profile["algorithmConfigurationName"],
                )
                for profile in vector_cfg.get("profiles", [])
            ],
        )

    return SearchIndex(name=schema["name"], fields=fields, vector_search=vector_search)


def _to_search_field(field: dict[str, Any]) -> Any:
    from azure.search.documents.indexes.models import (
        ComplexField,
        SearchField,
        SearchFieldDataType,
    )

    field_type = field["type"]
    if field_type == "Edm.ComplexType":
        return ComplexField(
            name=field["name"],
            fields=[_to_search_field(sub) for sub in field.get("fields", [])],
        )
    if field_type == "Collection(Edm.ComplexType)":
        return ComplexField(
            name=field["name"],
            collection=True,
            fields=[_to_search_field(sub) for sub in field.get("fields", [])],
        )

    kwargs: dict[str, Any] = {
        "name": field["name"],
        "type": _edm_type(field_type),
        "key": bool(field.get("key")),
        "searchable": bool(field.get("searchable")),
        "filterable": bool(field.get("filterable")),
        "sortable": bool(field.get("sortable")),
    }
    if "dimensions" in field:
        kwargs["vector_search_dimensions"] = field["dimensions"]
        kwargs["vector_search_profile_name"] = field.get("vectorSearchProfile")
    return SearchField(**kwargs)


def _edm_type(field_type: str) -> Any:
    from azure.search.documents.indexes.models import SearchFieldDataType

    if field_type == "Edm.String":
        return SearchFieldDataType.String
    if field_type == "Collection(Edm.String)":
        return SearchFieldDataType.Collection(SearchFieldDataType.String)
    if field_type == "Collection(Edm.Single)":
        return SearchFieldDataType.Collection(SearchFieldDataType.Single)
    raise ValueError(f"unsupported field type for theme-generation schema: {field_type}")


def create_theme_generation_index(
    *,
    index_name: str | None = None,
    dimensions: int | None = None,
    recreate: bool = False,
    index_client: Any | None = None,
) -> dict[str, Any]:
    """Create (or, when explicitly allowed, recreate) the POC index.

    Always passes the name guard first. ``recreate`` additionally requires
    ``THEME_GEN_ALLOW_RECREATE=1``. The SDK client is built lazily unless an
    ``index_client`` is injected (tests inject a fake).
    """
    name = assert_safe_theme_generation_index_name(resolve_index_name(index_name))
    dims = dimensions if dimensions is not None else embedding_dimensions()
    search_index = build_search_index(index_name=name, dimensions=dims)
    client = index_client or make_index_client()

    if recreate:
        if not recreate_allowed():
            raise PermissionError(
                f"recreate requires {_RECREATE_ENV}=1 (refusing to delete '{name}')"
            )
        try:
            client.delete_index(name)
        except Exception as exc:  # noqa: BLE001 - best-effort delete (e.g. not found)
            logger.info("delete_index('%s') skipped: %s", name, exc)
        client.create_index(search_index)
        action = "recreated"
    else:
        client.create_index(search_index)
        action = "created"

    return {"index_name": name, "action": action, "dimensions": dims}


__all__ = [
    "assert_safe_theme_generation_index_name",
    "recreate_allowed",
    "build_search_index",
    "search_index_from_schema",
    "create_theme_generation_index",
]
