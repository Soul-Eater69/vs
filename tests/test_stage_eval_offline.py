from __future__ import annotations

import json

import pytest

from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage
from vs_app.modules.stages.stage_catalog import get_allowed_stages, load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    BUSINESS_NEEDS_FIELD,
    BUSINESS_VALUE_STREAM_FIELD,
    build_theme_stage_ground_truth,
    build_ticket_stage_ground_truth,
    extract_raw_stage_mentions_from_business_needs,
    parse_business_value_stream,
)
from vs_app.modules.stages.stage_metrics import compute_stage_metrics, evaluate_stage_predictions
from vs_app.modules.stages.stage_selector import predict_value_stream_stages


def _catalog() -> dict:
    return {
        "Manage Utilization Management Program": {
            "value_stream_id": "VSR00168130",
            "stages": [
                {
                    "name": "Manage UM",
                    "id": "",
                    "description": "",
                    "aliases": [],
                }
            ],
        }
    }


def test_parse_business_value_stream_name_and_id() -> None:
    parsed = parse_business_value_stream(
        "Manage Utilization Management Program {VSR00168130}"
    )

    assert parsed["name"] == "Manage Utilization Management Program"
    assert parsed["id"] == "VSR00168130"


def test_business_needs_extracts_first_stage_from_pipe_segment() -> None:
    mentions = extract_raw_stage_mentions_from_business_needs(
        "Value Stage: Manage UM | Fertility PA | Prior Authorization..."
    )

    assert mentions[0]["raw_stage"] == "Manage UM"
    assert mentions[0]["source"] == "business_needs"


def test_duplicate_stage_mentions_collapse_to_one_canonical_stage() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            BUSINESS_VALUE_STREAM_FIELD: "Manage Utilization Management Program {VSR00168130}",
            BUSINESS_NEEDS_FIELD: "Value Stage: Manage UM | Fertility PA",
        },
    }
    children = [
        {
            "key": "GROUP-22805",
            "fields": {
                "summary": (
                    "CP 2026 Women's and Family Health : "
                    "Manage Utilization Management Program - Manage UM Operations (PA)"
                ),
                "status": {"name": "Cancelled"},
                "issuetype": {"name": "Epic"},
            },
        },
        {
            "key": "GROUP-22838",
            "fields": {
                "summary": (
                    "CP 2026 Women's and Family Health : "
                    "Manage Utilization Management Program - Manage UM Operations (Referral)"
                ),
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Epic"},
            },
        },
    ]

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_catalog(),
        child_issues=children,
    )

    assert [stage["canonical"] for stage in result["verified_stages"]] == ["Manage UM"]
    assert len(result["verified_stages"][0]["raw_mentions"]) == 3


def test_canonicalize_stage_exact_match() -> None:
    result = canonicalize_stage("Manage UM", ["Manage UM"])

    assert result["canonical"] == "Manage UM"
    assert result["match_method"] == "exact"


def test_canonicalize_stage_fuzzy_prefix_match() -> None:
    result = canonicalize_stage("Manage UM Operations (PA)", ["Manage UM"])

    assert result["canonical"] == "Manage UM"
    assert result["match_method"] == "fuzzy"


def test_canonicalize_stage_unresolved() -> None:
    result = canonicalize_stage("Appeals Intake", ["Manage UM"])

    assert result["canonical"] is None
    assert result["match_method"] == "unresolved"


def test_stage_catalog_loader_from_json(tmp_path) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "Manage Utilization Management Program": [
                    "Manage UM",
                    "Configure UM Program",
                ]
            }
        ),
        encoding="utf-8",
    )

    catalog = load_stage_catalog(path, source="json")

    assert get_allowed_stages("manage utilization management program", catalog) == [
        "Manage UM",
        "Configure UM Program",
    ]


class _FakeJira:
    def __init__(self, issues: dict[str, dict]) -> None:
        self.issues = issues

    async def get_issue(self, issue_key: str, *, fields=None, expand=True):
        return self.issues[issue_key]


@pytest.mark.anyio
async def test_ground_truth_builder_groups_verified_stages_by_value_stream() -> None:
    idmt = {
        "key": "IDMT-19761",
        "fields": {
            "summary": "CP 2026 Women's and Family Health",
            "issuetype": {"name": "Engagement Request"},
            "issuelinks": [
                {
                    "type": {"name": "implements"},
                    "outwardIssue": {
                        "key": "GROUP-22223",
                        "fields": {
                            "summary": "Theme",
                            "issuetype": {"name": "Theme"},
                        },
                    },
                }
            ],
        },
    }
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            "issuetype": {"name": "Theme"},
            BUSINESS_VALUE_STREAM_FIELD: "Manage Utilization Management Program {VSR00168130}",
            BUSINESS_NEEDS_FIELD: "Value Stage: Manage UM | Fertility PA",
            "subtasks": [],
        },
    }

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=_FakeJira({"IDMT-19761": idmt, "GROUP-22223": theme}),
        catalog=_catalog(),
    )

    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": ["Manage UM"]
    }


def test_stage_selector_drops_invalid_model_picks() -> None:
    def fake_llm(messages):
        return {
            "picks": [
                {"stage": "Not Allowed", "confidence": 1.0, "reason": "bad"},
                {"stage": "Manage UM", "confidence": 0.8, "reason": "good"},
                {"stage": "Manage UM", "confidence": 0.7, "reason": "duplicate"},
            ]
        }

    result = predict_value_stream_stages(
        idea_card_text="Prior authorization update",
        value_stream_name="Manage Utilization Management Program",
        allowed_stages=["Manage UM"],
        llm=fake_llm,
    )

    assert [row["stage"] for row in result["predicted_stages"]] == ["Manage UM"]
    assert any("Not Allowed" in warning for warning in result["warnings"])


def test_stage_metrics_precision_recall_f1_with_deduped_gt() -> None:
    metrics = compute_stage_metrics(["Manage UM", "Manage UM", "Configure UM"], ["Manage UM", "Extra"])

    assert metrics["gt_stages"] == ["Configure UM", "Manage UM"]
    assert metrics["correct_stages"] == ["Manage UM"]
    assert metrics["false_positives"] == ["Extra"]
    assert metrics["false_negatives"] == ["Configure UM"]
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["f1"] == 0.5

    aggregate = evaluate_stage_predictions(
        [{"ticket_id": "IDMT-1", "value_stream_name": "VS", **metrics}]
    )
    assert aggregate["summary"]["micro_f1"] == 0.5
