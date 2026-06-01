"""Azure AI Search index schema for the Theme-generation POC index.

One index holds two document types (``document_type`` = ``"idmt"`` | ``"theme"``).
Only IDMT documents are vector-search documents; theme documents carry readable
``content`` for use as prompt examples and are intentionally not designed for
vector retrieval (no ``content_vector`` is produced for them by the builder).

The schema is a plain dict (Azure REST index definition shape) so it can be
serialized, diffed, or handed to the SDK/REST API later. It is NOT wired into a
live index in this phase. ``properties`` is a defined complex object (not raw
JSON).

``embedding_dimensions`` defaults to 3072 for the intended embedding model
(``text-embedding-3-large``). This is only a default; callers should override it
to match whatever embedding model is actually deployed (e.g. 1536 for
``text-embedding-3-small`` / ``ada-002``-style deployments).
"""

from __future__ import annotations

from typing import Any

_VALUE_STREAM_SUBFIELDS = [
    {"name": "group_id", "type": "Edm.String", "filterable": True},
    {"name": "value_stream_id", "type": "Edm.String", "filterable": True},
    {"name": "value_stream_name", "type": "Edm.String", "searchable": True},
    {"name": "support_type", "type": "Edm.String", "filterable": True},
    {"name": "reason", "type": "Edm.String", "searchable": True},
    {"name": "evidence", "type": "Edm.String", "searchable": True},
]

_STAGE_SUBFIELDS = [
    {"name": "epic_id", "type": "Edm.String", "filterable": True},
    {"name": "stage_id", "type": "Edm.String", "filterable": True},
    {"name": "stage_name", "type": "Edm.String", "searchable": True},
    {"name": "support_type", "type": "Edm.String", "filterable": True},
    {"name": "reason", "type": "Edm.String", "searchable": True},
    {"name": "evidence", "type": "Edm.String", "searchable": True},
]


def build_theme_generation_index_schema(
    *,
    index_name: str = "idp_theme_generation_poc",
    # Default for text-embedding-3-large; override to match the deployed model.
    embedding_dimensions: int = 3072,
    vector_profile_name: str = "theme-gen-vector-profile",
) -> dict[str, Any]:
    fields = [
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
        {"name": "document_type", "type": "Edm.String", "filterable": True},
        {"name": "ticket_id", "type": "Edm.String", "filterable": True, "sortable": True},
        {"name": "group_id", "type": "Edm.String", "filterable": True},
        {"name": "content", "type": "Edm.String", "searchable": True},
        # Only IDMT documents populate content_vector (see module docstring).
        {
            "name": "content_vector",
            "type": "Collection(Edm.Single)",
            "searchable": True,
            "dimensions": embedding_dimensions,
            "vectorSearchProfile": vector_profile_name,
        },
        {
            "name": "properties",
            "type": "Edm.ComplexType",
            "fields": [
                {"name": "summary_text", "type": "Edm.String", "searchable": True},
                {"name": "idmt_description", "type": "Edm.String", "searchable": True},
                {"name": "theme_description", "type": "Edm.String", "searchable": True},
                {"name": "business_needs", "type": "Edm.String", "searchable": True},
                _string_collection("key_terms"),
                _string_collection("stakeholders"),
                _string_collection("systems_and_products"),
                {
                    "name": "value_streams",
                    "type": "Collection(Edm.ComplexType)",
                    "fields": [dict(subfield) for subfield in _VALUE_STREAM_SUBFIELDS],
                },
                {
                    "name": "stages",
                    "type": "Collection(Edm.ComplexType)",
                    "fields": [dict(subfield) for subfield in _STAGE_SUBFIELDS],
                },
            ],
        },
    ]

    return {
        "name": index_name,
        "fields": fields,
        "vectorSearch": {
            "algorithms": [{"name": "theme-gen-hnsw", "kind": "hnsw"}],
            "profiles": [
                {"name": vector_profile_name, "algorithmConfigurationName": "theme-gen-hnsw"}
            ],
        },
    }


def _string_collection(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Collection(Edm.String)",
        "searchable": True,
        "filterable": True,
    }


__all__ = ["build_theme_generation_index_schema"]
