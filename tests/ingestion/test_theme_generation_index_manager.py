"""Tests for the Theme-generation POC index manager (fakes only; no Azure)."""

from __future__ import annotations

import pytest

from vs_app.ingestion.upload import azure_search_client as client_mod
from vs_app.ingestion.upload.index_manager import (
    assert_safe_theme_generation_index_name,
    build_search_index,
    create_theme_generation_index,
    search_index_from_schema,
)
from vs_app.ingestion.index_documents.theme_generation_schema import (
    build_theme_generation_index_schema,
)

POC_INDEX = client_mod.DEFAULT_THEME_GENERATION_INDEX_NAME


class FakeIndexClient:
    def __init__(self) -> None:
        self.created: list = []
        self.deleted: list[str] = []

    def create_index(self, index):
        self.created.append(index)

    def delete_index(self, name):
        self.deleted.append(name)


def test_create_index_calls_client_once_with_poc_index() -> None:
    fake = FakeIndexClient()
    result = create_theme_generation_index(index_name=POC_INDEX, index_client=fake)
    assert result["action"] == "created"
    assert len(fake.created) == 1
    assert fake.created[0].name == POC_INDEX
    assert fake.deleted == []


def test_recreate_requires_env_flag(monkeypatch) -> None:
    monkeypatch.delenv("THEME_GEN_ALLOW_RECREATE", raising=False)
    fake = FakeIndexClient()
    with pytest.raises(PermissionError):
        create_theme_generation_index(index_name=POC_INDEX, recreate=True, index_client=fake)
    assert fake.created == [] and fake.deleted == []


def test_recreate_with_env_flag_deletes_then_creates(monkeypatch) -> None:
    monkeypatch.setenv("THEME_GEN_ALLOW_RECREATE", "1")
    fake = FakeIndexClient()
    result = create_theme_generation_index(index_name=POC_INDEX, recreate=True, index_client=fake)
    assert result["action"] == "recreated"
    assert fake.deleted == [POC_INDEX]
    assert len(fake.created) == 1


def test_name_guard_refuses_empty_and_protected_and_other(monkeypatch) -> None:
    monkeypatch.setenv("THEME_GEN_ALLOW_RECREATE", "1")
    with pytest.raises(ValueError):
        assert_safe_theme_generation_index_name("")
    # A protected (value-stream/historical/summary) name is refused.
    for protected in client_mod.PROTECTED_INDEX_NAMES:
        with pytest.raises(ValueError):
            assert_safe_theme_generation_index_name(protected)
    # Any non-configured name is refused, and the client is never touched.
    fake = FakeIndexClient()
    with pytest.raises(ValueError):
        create_theme_generation_index(index_name="some-other-index", recreate=True, index_client=fake)
    assert fake.created == [] and fake.deleted == []


def test_schema_conversion_preserves_complex_properties() -> None:
    index = search_index_from_schema(build_theme_generation_index_schema(index_name=POC_INDEX))
    by_name = {field.name: field for field in index.fields}
    assert {"id", "document_type", "ticket_id", "group_id", "content", "content_vector", "properties"} <= set(by_name)

    properties = by_name["properties"]
    sub = {field.name: field for field in properties.fields}
    assert {"value_streams", "stages"} <= set(sub)

    vs_subfields = {field.name for field in sub["value_streams"].fields}
    assert vs_subfields == {
        "group_id",
        "value_stream_id",
        "value_stream_name",
        "support_type",
        "reason",
        "evidence",
    }
    stage_subfields = {field.name for field in sub["stages"].fields}
    assert stage_subfields == {
        "epic_id",
        "stage_id",
        "stage_name",
        "support_type",
        "reason",
        "evidence",
    }
    # value_streams/stages are complex collections.
    assert "Collection(Edm.ComplexType)" in str(sub["value_streams"].type)


def test_embedding_dimensions_use_configured_value() -> None:
    expected = client_mod.embedding_dimensions()
    index = build_search_index(index_name=POC_INDEX)
    content_vector = next(f for f in index.fields if f.name == "content_vector")
    assert content_vector.vector_search_dimensions == expected

    overridden = build_search_index(index_name=POC_INDEX, dimensions=1536)
    cv = next(f for f in overridden.fields if f.name == "content_vector")
    assert cv.vector_search_dimensions == 1536
