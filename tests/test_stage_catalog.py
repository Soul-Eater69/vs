from __future__ import annotations

import json

import pytest

from vs_app.modules.stages.catalog import (
    extract_stages_from_value_stream_row,
    get_stages_for_value_stream,
    normalize_stage,
)
from vs_app.modules.stages.stage_catalog import (
    get_allowed_stages,
    load_stage_catalog,
    summarize_stage_catalog,
)


def _properties() -> dict:
    return {
        "stages": [
            {
                "value_stream_stage_id": "VSS2",
                "value_stream_stage_sequence": "2",
                "value_stream_stage_name": "Second Stage",
                "value_stream_stage_description": "second",
            },
            {
                "value_stream_stage_id": "VSS1",
                "value_stream_stage_sequence": "1",
                "value_stream_stage_name": "First Stage",
                "value_stream_stage_description": "first",
            },
        ]
    }


def test_normalize_stage_maps_value_stream_stage_keys() -> None:
    stage = normalize_stage(
        {
            "value_stream_stage_id": "VSS01261919",
            "value_stream_stage_sequence": "1",
            "value_stream_stage_name": "Define Incident Scope",
            "value_stream_stage_display_name": "Define Incident Scope {VSS01261919}",
            "value_stream_stage_description": "The act of gleaning applicable data.",
            "value_stream_stage_entrance_criteria": "Privacy incident identified",
            "value_stream_stage_exit_criteria": "Scope understood",
            "value_stream_stage_value_items": "Understanding of incident scope",
            "value_stream_stage_stakeholders": "Privacy Office",
        }
    )

    assert stage == {
        "stage_id": "VSS01261919",
        "stage_sequence": 1,
        "stage_name": "Define Incident Scope",
        "stage_display_name": "Define Incident Scope {VSS01261919}",
        "stage_description": "The act of gleaning applicable data.",
        "stage_entrance_criteria": "Privacy incident identified",
        "stage_exit_criteria": "Scope understood",
        "stage_value_items": "Understanding of incident scope",
        "stage_stakeholders": "Privacy Office",
    }


def test_extract_stages_from_properties_json_string_sorts_by_sequence() -> None:
    stages = extract_stages_from_value_stream_row(
        {
            "entity_id": "VSR1",
            "entity_name": "Example Stream",
            "properties": json.dumps(_properties()),
        }
    )

    assert [stage["stage_id"] for stage in stages] == ["VSS1", "VSS2"]


def test_extract_stages_from_properties_dict() -> None:
    stages = extract_stages_from_value_stream_row(
        {
            "entity_id": "VSR1",
            "entity_name": "Example Stream",
            "properties": _properties(),
        }
    )

    assert [stage["stage_name"] for stage in stages] == ["First Stage", "Second Stage"]


def test_lookup_returns_only_selected_value_stream_stages_by_id() -> None:
    class FakeClient:
        def search_all(self, *, search_text, filter_expression, select):
            assert "entity_id eq 'VSR1'" in filter_expression
            return [
                {
                    "entity_id": "VSR1",
                    "entity_name": "Example Stream",
                    "properties": _properties(),
                }
            ]

    stages = get_stages_for_value_stream("VSR1", "Other Stream", client=FakeClient())

    assert [stage["stage_id"] for stage in stages] == ["VSS1", "VSS2"]


def test_lookup_falls_back_to_normalized_name() -> None:
    class FakeClient:
        def search_all(self, *, search_text, filter_expression, select):
            if "entity_name eq" in filter_expression:
                return []
            return [
                {
                    "entity_id": "VSR2",
                    "entity_name": "Manage Leads and Opportunities",
                    "properties": _properties(),
                }
            ]

    stages = get_stages_for_value_stream(
        None,
        "Manage Leads & Opportunities",
        client=FakeClient(),
    )

    assert [stage["stage_id"] for stage in stages] == ["VSS1", "VSS2"]


def test_stage_catalog_loader_supports_dict_of_stage_strings(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            {
                "Manage Utilization Management Program": [
                    "Manage UM",
                    "Review UM",
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    assert get_allowed_stages("Manage Utilization Management Program", catalog) == [
        "Manage UM",
        "Review UM",
    ]


def test_stage_catalog_loader_supports_dict_with_stages_list(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            {
                "Manage Utilization Management Program": {
                    "stages": [
                        "Manage UM",
                        "Review UM",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    assert get_allowed_stages("manage utilization management program", catalog) == [
        "Manage UM",
        "Review UM",
    ]


def test_stage_catalog_loader_supports_list_rows_with_stage_name(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            [
                {
                    "value_stream_name": "Manage Utilization Management Program",
                    "value_stream_id": "VSR00168130",
                    "stage_name": "Manage UM",
                    "stage_id": "VSS1",
                },
                {
                    "value_stream": "Manage Utilization Management Program",
                    "stage": "Review UM",
                    "stage_id": "VSS2",
                },
            ]
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    entry = catalog["Manage Utilization Management Program"]
    assert entry["value_stream_id"] == "VSR00168130"
    assert get_allowed_stages("Manage Utilization Management Program", catalog) == [
        "Manage UM",
        "Review UM",
    ]
    assert [stage["id"] for stage in entry["stages"]] == ["VSS1", "VSS2"]


def test_stage_catalog_loader_supports_list_rows_with_stages_array(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            [
                {
                    "value_stream_name": "Manage Utilization Management Program",
                    "value_stream_id": "VSR00168130",
                    "stages": ["Manage UM", "Review UM"],
                }
            ]
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    assert get_allowed_stages("Manage Utilization Management Program", catalog) == [
        "Manage UM",
        "Review UM",
    ]


def test_stage_catalog_loader_supports_actual_value_stream_stage_map_schema(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            [
                {
                    "value_stream_id": "VSR00168130",
                    "value_stream_name": "Manage Utilization Management Program",
                    "value_stream_description": (
                        "The end-to-end perspective for managing all the components "
                        "to a comprehensive utilization management program."
                    ),
                    "value_stream_category": "Manage Medical Care and Wellness",
                    "value_stream_trigger": None,
                    "value_stream_stakeholders": "Medical Director; Government Operations",
                    "value_stream_created_date": "2022-02-17",
                    "stages": [
                        {
                            "stage_id": "VSS00939329",
                            "stage_sequence": 1,
                            "stage_name": "Manage UM Guidelines",
                            "stage_display_name": "Manage UM Guidelines {VSS00939329}",
                            "stage_description": "The act of identifying / modifying UM standards.",
                            "stage_entrance_criteria": "Request to review and/or modify UM Program.",
                            "stage_exit_criteria": "Established/Modified UM Program.",
                            "stage_value_items": "PA Grid; UM Program",
                            "stage_stakeholders": "Senior Management; BCBSA; Legal",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    entry = catalog["Manage Utilization Management Program"]
    assert entry["value_stream_id"] == "VSR00168130"
    assert entry["description"].startswith("The end-to-end perspective")
    assert entry["category"] == "Manage Medical Care and Wellness"
    assert entry["stakeholders"] == "Medical Director; Government Operations"
    assert get_allowed_stages("Manage Utilization Management Program", catalog) == [
        "Manage UM Guidelines"
    ]
    assert entry["stages"][0] == {
        "name": "Manage UM Guidelines",
        "id": "VSS00939329",
        "description": "The act of identifying / modifying UM standards.",
        "sequence": 1,
        "display_name": "Manage UM Guidelines {VSS00939329}",
        "entrance_criteria": "Request to review and/or modify UM Program.",
        "exit_criteria": "Established/Modified UM Program.",
        "value_items": "PA Grid; UM Program",
        "stakeholders": "Senior Management; BCBSA; Legal",
        "aliases": [],
    }


def test_stage_catalog_loader_supports_wrapped_value_streams_map_schema(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps(
            {
                "source_file": "value_stream_stage_map.txt",
                "value_stream_count": 1,
                "value_streams": [
                    {
                        "value_stream_id": "VSR00168130",
                        "value_stream_name": "Manage Utilization Management Program",
                        "value_stream_description": "The end-to-end perspective.",
                        "stages": [
                            {
                                "stage_id": "VSS00939329",
                                "stage_sequence": 1,
                                "stage_name": "Manage UM Guidelines",
                            },
                            {
                                "stage_id": "VSS00939330",
                                "stage_sequence": 2,
                                "stage_name": "Review UM Program",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path=path, source="json")

    assert get_allowed_stages("Manage Utilization Management Program", catalog) == [
        "Manage UM Guidelines",
        "Review UM Program",
    ]
    assert summarize_stage_catalog(catalog) == [
        {
            "value_stream": "Manage Utilization Management Program",
            "stage_count": 2,
            "stages": ["Manage UM Guidelines", "Review UM Program"],
        }
    ]


def test_summarize_stage_catalog_returns_stage_counts(tmp_path) -> None:
    path = tmp_path / "value_stream_stage_map.json"
    path.write_text(
        json.dumps({"Example Stream": ["First Stage", "Second Stage"]}),
        encoding="utf-8",
    )
    catalog = load_stage_catalog(path=path, source="json")

    assert summarize_stage_catalog(catalog) == [
        {
            "value_stream": "Example Stream",
            "stage_count": 2,
            "stages": ["First Stage", "Second Stage"],
        }
    ]


def test_stage_catalog_loader_helpful_error_for_unexpected_json_shape(tmp_path) -> None:
    path = tmp_path / "bad_stage_map.json"
    path.write_text(json.dumps({"unexpected": {"not_stages": ["Nope"]}}), encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        load_stage_catalog(path=path, source="json")

    message = str(excinfo.value)
    assert str(path) in message
    assert "top-level JSON type: dict" in message
    assert "unexpected" in message
    assert "Expected supported shapes" in message
