from __future__ import annotations

import json

from vs_app.modules.stages.catalog import (
    extract_stages_from_value_stream_row,
    get_stages_for_value_stream,
    normalize_stage,
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
