"""Azure AI Search index schema for the canonical IDMT document.

The schema is a plain dict (Azure REST index definition shape) so it can be
serialized, diffed, or handed to either the SDK or the REST API later. It is
not wired into a live index in this phase. Embedding dimensions default to 3072
to match ``text-embedding-3-large``.
"""

from __future__ import annotations

from typing import Any

_SUPPORT_SUBFIELDS = [
    {"name": "value_stream_name", "type": "Edm.String", "searchable": True},
    {"name": "value_stream_id", "type": "Edm.String", "filterable": True},
    {"name": "support_type", "type": "Edm.String", "filterable": True},
    {"name": "reason", "type": "Edm.String", "searchable": True},
    {"name": "evidence", "type": "Edm.String", "searchable": True},
    {"name": "source", "type": "Edm.String", "filterable": True},
    {"name": "confidence", "type": "Edm.Double", "filterable": True, "sortable": True},
]


def build_idmt_index_schema(
    *,
    index_name: str = "idp_idmt_data_v2",
    embedding_dimensions: int = 3072,
    vector_profile_name: str = "idmt-vector-profile",
) -> dict[str, Any]:
    fields = [
        # Identity
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
        {"name": "ticket_id", "type": "Edm.String", "filterable": True, "sortable": True},
        {"name": "ticket_key", "type": "Edm.String", "filterable": True},
        # Text
        _text("summary"),
        _text("description"),
        _text("idea_card_text"),
        _text("attachment_text"),
        _text("extracted_text"),
        _text("generated_summary"),
        _text("retrieval_text"),
        # Vectors
        _vector("summary_vector", embedding_dimensions, vector_profile_name),
        _vector("context_vector", embedding_dimensions, vector_profile_name),
        # Value Stream
        _string_collection("valid_value_stream_names", searchable=True),
        _string_collection("valid_value_stream_ids"),
        _string_collection("value_stream_pairs"),
        _complex_collection("value_stream_support"),
        _text("value_stream_support_text"),
        # Stage
        # Reconstruction/debug JSON: retrievable for rebuilding/inspection,
        # but not full-text searchable. Search uses stage_names, stage_pairs,
        # stage_history_text, and stage_support_text instead.
        {
            "name": "gt_by_value_stream_json",
            "type": "Edm.String",
            "searchable": False,
            "retrievable": True,
        },
        _string_collection("stage_names", searchable=True),
        _string_collection("stage_ids"),
        _string_collection("stage_pairs"),
        _text("stage_history_text"),
        _complex_collection("stage_support"),
        _text("stage_support_text"),
        {"name": "has_stage_gt", "type": "Edm.Boolean", "filterable": True},
        {"name": "stage_count", "type": "Edm.Int32", "filterable": True, "sortable": True},
        # Metadata
        {"name": "has_valid_value_streams", "type": "Edm.Boolean", "filterable": True},
        {
            "name": "valid_value_stream_count",
            "type": "Edm.Int32",
            "filterable": True,
            "sortable": True,
        },
        {"name": "source_system", "type": "Edm.String", "filterable": True},
        {"name": "index_version", "type": "Edm.String", "filterable": True},
        {
            "name": "ingested_at",
            "type": "Edm.DateTimeOffset",
            "filterable": True,
            "sortable": True,
        },
        _string_collection("warnings"),
    ]

    return {
        "name": index_name,
        "fields": fields,
        "vectorSearch": {
            "algorithms": [{"name": "idmt-hnsw", "kind": "hnsw"}],
            "profiles": [
                {"name": vector_profile_name, "algorithmConfigurationName": "idmt-hnsw"}
            ],
        },
    }


def _text(name: str) -> dict[str, Any]:
    return {"name": name, "type": "Edm.String", "searchable": True}


def _string_collection(name: str, *, searchable: bool = False) -> dict[str, Any]:
    field: dict[str, Any] = {
        "name": name,
        "type": "Collection(Edm.String)",
        "filterable": True,
    }
    if searchable:
        field["searchable"] = True
    return field


def _vector(name: str, dimensions: int, profile_name: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Collection(Edm.Single)",
        "searchable": True,
        "dimensions": dimensions,
        "vectorSearchProfile": profile_name,
    }


def _complex_collection(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "Collection(Edm.ComplexType)",
        "fields": [dict(subfield) for subfield in _SUPPORT_SUBFIELDS],
    }


__all__ = ["build_idmt_index_schema"]
