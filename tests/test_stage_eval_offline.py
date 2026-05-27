from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_stage_batch import _idea_card_text

from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage
from vs_app.modules.stages.stage_catalog import get_allowed_stages, load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    BUSINESS_NEEDS_FIELD,
    BUSINESS_VALUE_STREAM_FIELD,
    _collect_child_issues,
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


def _operations_catalog() -> dict:
    return {
        "Manage Utilization Management Program": {
            "value_stream_id": "VSR00168130",
            "stages": [
                {"name": "Manage UM Guidelines", "id": "", "description": "", "aliases": []},
                {"name": "Manage Clinical Guidelines", "id": "", "description": "", "aliases": []},
                {"name": "Manage UM Operations", "id": "", "description": "", "aliases": []},
                {"name": "Evaluate UM Performance", "id": "", "description": "", "aliases": []},
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


def test_business_needs_does_not_create_stage_ground_truth() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            BUSINESS_VALUE_STREAM_FIELD: "Manage Utilization Management Program {VSR00168130}",
            BUSINESS_NEEDS_FIELD: "Value Stage: Deliver Response",
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog={
            "Manage Utilization Management Program": {
                "stages": [{"name": "Deliver Response", "id": "", "description": "", "aliases": []}]
            }
        },
        child_issues=[],
    )

    assert result["verified_stages"] == []
    assert result["business_needs_mentions_debug_only"]
    assert result["business_needs_mentions_debug_only"][0]["raw_stage"] == "Deliver Response"


def test_child_epic_summary_creates_stage_ground_truth() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            BUSINESS_VALUE_STREAM_FIELD: "Manage Utilization Management Program {VSR00168130}",
            BUSINESS_NEEDS_FIELD: "Value Stage: Deliver Response",
        },
    }
    child = {
        "key": "GROUP-22805",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (PA)"
            ),
            "status": {"name": "Cancelled"},
            "issuetype": {"name": "Epic"},
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_operations_catalog(),
        child_issues=[child],
    )

    assert result["verified_stages"][0]["canonical"] == "Manage UM Operations"
    assert result["verified_stages"][0]["raw_mentions"][0]["raw"] == "Manage UM Operations"
    assert result["child_issue_mentions_debug"][0]["raw_stage"] == "Manage UM Operations"
    assert result["child_issue_mentions_debug"][0]["source_text"].endswith(
        "Manage UM Operations (PA)"
    )
    assert result["canonicalization_debug"][0]["child_key"] == "GROUP-22805"
    assert result["canonicalization_debug"][0]["canonical"] == "Manage UM Operations"
    assert result["canonicalization_debug"][0]["match_method"] == "exact"


def test_duplicate_child_epic_stages_collapse_to_one_canonical_stage() -> None:
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
        catalog=_operations_catalog(),
        child_issues=children,
    )

    assert len(result["verified_stages"]) == 1
    assert result["verified_stages"][0]["canonical"] == "Manage UM Operations"
    assert len(result["verified_stages"][0]["raw_mentions"]) == 2
    assert [row["child_key"] for row in result["verified_stages"][0]["raw_mentions"]] == [
        "GROUP-22805",
        "GROUP-22838",
    ]


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


class _SearchFakeJira(_FakeJira):
    def __init__(
        self,
        issues: dict[str, dict],
        search_results: dict[str, list[dict]] | None = None,
        error_jqls: set[str] | None = None,
    ) -> None:
        super().__init__(issues)
        self.search_results = search_results or {}
        self.error_jqls = error_jqls or set()
        self.seen_jqls: list[str] = []

    async def search_issues(self, jql: str, start_at=0, max_results=100, fields=None):
        self.seen_jqls.append(jql)
        if jql in self.error_jqls:
            raise RuntimeError("unsupported JQL")
        issues = list(self.search_results.get(jql) or [])
        return {"issues": issues, "total": len(issues)}


@pytest.mark.anyio
async def test_ground_truth_builder_groups_verified_stages_by_value_stream() -> None:
    idmt = {
        "key": "IDMT-19761",
        "fields": {
            "summary": "CP 2026 Women's and Family Health",
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "IDMT-only description text."}
                        ],
                    }
                ],
            },
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
            "subtasks": [
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
                }
            ],
        },
    }

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=_FakeJira({"IDMT-19761": idmt, "GROUP-22223": theme}),
        catalog=_operations_catalog(),
    )

    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": ["Manage UM Operations"]
    }
    assert result["idmt_description"] == "IDMT-only description text."


@pytest.mark.anyio
async def test_child_lookup_uses_reverse_parent_link_jql() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            "subtasks": [],
        },
    }
    child_a = {
        "key": "GROUP-22805",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (PA)"
            ),
            "status": {"name": "Cancelled"},
            "issuetype": {"name": "Epic"},
            "customfield_11401": "GROUP-22223",
        },
    }
    child_b = {
        "key": "GROUP-22838",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (Referral)"
            ),
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Epic"},
            "customfield_11401": "GROUP-22223",
        },
    }
    jira = _SearchFakeJira(
        {"GROUP-22223": theme},
        search_results={
            'issuekey in childIssuesOf("GROUP-22223")': [child_a, child_b],
            '"Parent Link" = GROUP-22223': [child_a, child_b],
            "parent = GROUP-22223": [],
        },
    )

    child_issues, lookup = await _collect_child_issues(
        jira_client=jira,
        theme_issue=theme,
        fetch_child_issues=True,
    )

    assert [issue["key"] for issue in child_issues] == ["GROUP-22805", "GROUP-22838"]
    assert lookup["child_issue_keys"] == ["GROUP-22805", "GROUP-22838"]
    assert [attempt["jql"] for attempt in lookup["jql_attempts"]] == [
        'issuekey in childIssuesOf("GROUP-22223")',
        '"Parent Link" = GROUP-22223',
        "parent = GROUP-22223",
    ]
    assert lookup["jql_attempts"][0]["count"] == 2
    assert lookup["jql_attempts"][1]["count"] == 2
    assert lookup["jql_attempts"][2]["count"] == 0


@pytest.mark.anyio
async def test_unsupported_child_lookup_jql_is_recorded_and_gt_continues() -> None:
    idmt = {
        "key": "IDMT-19761",
        "fields": {
            "summary": "CP 2026 Women's and Family Health",
            "description": "IDMT-only description text.",
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
            BUSINESS_NEEDS_FIELD: "Value Stage: Deliver Response",
            "subtasks": [],
        },
    }
    child = {
        "key": "GROUP-22805",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (PA)"
            ),
            "status": {"name": "Cancelled"},
            "issuetype": {"name": "Epic"},
            "customfield_11401": "GROUP-22223",
        },
    }
    jira = _SearchFakeJira(
        {"IDMT-19761": idmt, "GROUP-22223": theme},
        search_results={
            '"Parent Link" = GROUP-22223': [child],
            "parent = GROUP-22223": [],
        },
        error_jqls={'issuekey in childIssuesOf("GROUP-22223")'},
    )

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=jira,
        catalog=_operations_catalog(),
        fetch_child_issues=True,
    )

    theme_gt = result["linked_themes"][0]
    attempts = theme_gt["child_issue_lookup"]["jql_attempts"]
    assert attempts[0]["jql"] == 'issuekey in childIssuesOf("GROUP-22223")'
    assert "unsupported JQL" in attempts[0]["error"]
    assert theme_gt["child_issue_lookup"]["child_issue_keys"] == ["GROUP-22805"]
    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": ["Manage UM Operations"]
    }


@pytest.mark.anyio
async def test_stage_eval_fallback_uses_only_idmt_summary_and_description() -> None:
    ticket = {
        "idmt_summary": "IDMT summary only",
        "idmt_description": "IDMT description only",
        "linked_themes": [
            {
                "theme_summary": "LEAK Theme summary",
                "business_needs_raw": "LEAK Value Stage: Manage UM",
                "verified_stages": [{"canonical": "LEAK verified"}],
                "child_issues": [{"summary": "LEAK child issue"}],
            }
        ],
        "gt_by_value_stream": {"LEAK VS": ["LEAK stage"]},
    }

    text = await _idea_card_text("IDMT-19761", ticket, jira_client=None)

    assert text == "IDMT summary only\n\nIDMT description only"
    assert "LEAK Theme summary" not in text
    assert "LEAK Value Stage" not in text
    assert "LEAK verified" not in text
    assert "LEAK child issue" not in text
    assert "LEAK VS" not in text


@pytest.mark.anyio
async def test_stage_eval_fallback_returns_summary_when_description_missing() -> None:
    ticket = {
        "idmt_summary": "IDMT summary only",
        "linked_themes": [
            {
                "theme_summary": "LEAK Theme summary",
                "business_needs_raw": "LEAK Business Needs",
            }
        ],
    }

    text = await _idea_card_text("IDMT-19761", ticket, jira_client=None)

    assert text == "IDMT summary only"
    assert "LEAK" not in text


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
