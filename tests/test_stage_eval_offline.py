from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_stage_batch import _evaluate_ticket, _idea_card_text

from vs_app.modules.stages.stage_canonicalizer import canonicalize_stage
from vs_app.modules.stages.stage_catalog import get_allowed_stages, load_stage_catalog
from vs_app.modules.stages.stage_ground_truth import (
    BUSINESS_NEEDS_FIELD,
    BUSINESS_VALUE_STREAM_FIELD,
    build_theme_stage_ground_truth,
    build_ticket_stage_ground_truth,
    extract_stage_from_child_epic_summary,
    extract_raw_stage_mentions_from_business_needs,
    fetch_direct_child_epics,
    generate_stage_candidates_from_child_epic_summary,
    parse_business_value_stream,
    find_linked_theme_issues,
    resolve_child_epic_stage,
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


def _leads_catalog() -> dict:
    return {
        "Manage Leads and opportunities": {
            "value_stream_id": "VSR-LEADS",
            "stages": [
                {"name": "Perform Outreach to Leads and Prospects", "id": "", "description": "", "aliases": []},
                {"name": "Product Sales Training", "id": "", "description": "", "aliases": []},
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


def test_idmt_issue_links_find_themes() -> None:
    issue = {
        "key": "IDMT-19761",
        "fields": {
            "issuelinks": [
                {
                    "type": {"name": "implements"},
                    "outwardIssue": {
                        "key": "GROUP-22223",
                        "fields": {
                            "summary": "Theme summary",
                            "issuetype": {"name": "Theme"},
                        },
                    },
                },
                {
                    "type": {"name": "relates"},
                    "outwardIssue": {
                        "key": "GROUP-99999",
                        "fields": {"issuetype": {"name": "Theme"}},
                    },
                },
            ]
        },
    }

    assert find_linked_theme_issues(issue) == [
        {"key": "GROUP-22223", "summary": "Theme summary", "issue_type": "Theme"}
    ]


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
        child_epics=[],
        child_lookup_debug={},
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
    mention = extract_stage_from_child_epic_summary(
        child_issue=child,
        value_stream_name="Manage Utilization Management Program",
    )

    assert mention is not None
    assert mention["raw_stage"] == "Manage UM Operations"
    assert mention["source"] == "child_epic_summary"

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_operations_catalog(),
        child_epics=[child],
        child_lookup_debug={},
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


def test_child_epic_candidate_resolver_handles_compact_hyphens() -> None:
    child = {
        "key": "GROUP-22722",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : Manage Leads and opportunities- "
                "Perform Outreach- Product Sales Training"
            ),
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Epic"},
        },
    }

    raw_summary, candidates = generate_stage_candidates_from_child_epic_summary(
        child_issue=child,
        value_stream_name="Manage Leads and opportunities",
    )
    resolution = resolve_child_epic_stage(
        child_issue=child,
        allowed_stages=_leads_catalog()["Manage Leads and opportunities"]["stages"],
        value_stream_name="Manage Leads and opportunities",
    )

    assert raw_summary == child["fields"]["summary"]
    assert [row["candidate"] for row in candidates] == [
        "Perform Outreach",
        "Product Sales Training",
        "Perform Outreach Product Sales Training",
        child["fields"]["summary"],
    ]
    assert resolution["included"] is True
    assert resolution["cleaned_stage_name"] == "Perform Outreach"
    assert resolution["canonical_stage"] == "Perform Outreach to Leads and Prospects"
    assert resolution["match_method"] == "rapidfuzz"
    assert resolution["selected_candidate_rule"] == "first_suffix_segment"


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
        child_epics=children,
        child_lookup_debug={},
    )

    assert len(result["verified_stages"]) == 1
    assert result["verified_stages"][0]["canonical"] == "Manage UM Operations"
    assert len(result["verified_stages"][0]["raw_mentions"]) == 2
    assert [row["child_key"] for row in result["verified_stages"][0]["raw_mentions"]] == [
        "GROUP-22805",
        "GROUP-22838",
    ]


def test_child_epic_candidate_resolution_populates_theme_debug() -> None:
    theme = {
        "key": "GROUP-22219",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Leads and opportunities",
            BUSINESS_VALUE_STREAM_FIELD: "Manage Leads and opportunities {VSR-LEADS}",
            BUSINESS_NEEDS_FIELD: "",
        },
    }
    child = {
        "key": "GROUP-22722",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : Manage Leads and opportunities- "
                "Perform Outreach- Product Sales Training"
            ),
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Epic"},
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_leads_catalog(),
        child_epics=[child],
        child_lookup_debug={},
    )

    resolution = result["child_issue_stage_resolution_debug"][0]
    assert resolution["child_key"] == "GROUP-22722"
    assert resolution["cleaned_stage_name"] == "Perform Outreach"
    assert resolution["canonical_stage"] == "Perform Outreach to Leads and Prospects"
    assert resolution["included"] is True
    assert result["child_issue_mentions_debug"] == [
        {
            "raw_stage": "Perform Outreach",
            "source": "child_epic_summary",
            "source_text": child["fields"]["summary"],
            "child_key": "GROUP-22722",
        }
    ]
    assert result["canonicalization_debug"][0]["canonical"] == "Perform Outreach to Leads and Prospects"
    assert [stage["canonical"] for stage in result["verified_stages"]] == [
        "Perform Outreach to Leads and Prospects"
    ]


def test_canonicalize_stage_exact_match() -> None:
    result = canonicalize_stage("Manage UM", ["Manage UM"])

    assert result["canonical"] == "Manage UM"
    assert result["match_method"] == "exact"


def test_canonicalize_stage_fuzzy_prefix_match() -> None:
    result = canonicalize_stage("Manage UM Operations (PA)", ["Manage UM"])

    assert result["canonical"] == "Manage UM"
    assert result["match_method"] == "rapidfuzz"


def test_canonicalize_stage_rapidfuzz_partial_match() -> None:
    result = canonicalize_stage(
        "Perform Outreach",
        ["Perform Outreach to Leads and Prospects"],
    )

    assert result["canonical"] == "Perform Outreach to Leads and Prospects"
    assert result["match_method"] == "rapidfuzz"
    assert result["confidence"] >= 0.86


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
        if "childIssuesOf" in jql:
            raise AssertionError("childIssuesOf must not be called for stage GT")
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
        },
    }

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=_SearchFakeJira(
            {"IDMT-19761": idmt, "GROUP-22223": theme},
            search_results={'"Parent Link" = GROUP-22223 AND issuetype = Epic': [child]},
        ),
        catalog=_operations_catalog(),
    )

    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": ["Manage UM Operations"]
    }
    assert result["idmt_description"] == "IDMT-only description text."


@pytest.mark.anyio
async def test_parent_link_lookup_returns_child_epics() -> None:
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
        {},
        search_results={
            '"Parent Link" = GROUP-22223 AND issuetype = Epic': [child_a, child_b],
        },
    )

    child_issues, lookup = await fetch_direct_child_epics(
        jira_client=jira,
        theme_key="GROUP-22223",
    )

    assert [issue["key"] for issue in child_issues] == ["GROUP-22805", "GROUP-22838"]
    assert lookup["child_issue_keys"] == ["GROUP-22805", "GROUP-22838"]
    assert lookup["lookup_strategy"] == "parent_link_only"
    assert lookup["jql_attempts"][0]["jql"] == '"Parent Link" = GROUP-22223 AND issuetype = Epic'
    assert lookup["jql_attempts"][0]["count"] == 2
    assert jira.seen_jqls == ['"Parent Link" = GROUP-22223 AND issuetype = Epic']


@pytest.mark.anyio
async def test_no_parent_link_children_leaves_stage_ground_truth_empty() -> None:
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
    jira = _SearchFakeJira(
        {"IDMT-19761": idmt, "GROUP-22223": theme},
        search_results={
            '"Parent Link" = GROUP-22223 AND issuetype = Epic': [],
        },
    )

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=jira,
        catalog=_operations_catalog(),
    )

    theme_gt = result["linked_themes"][0]
    assert theme_gt["verified_stages"] == []
    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": []
    }
    assert any(
        "no direct Parent Link child Epics found" in warning
        for warning in theme_gt["warnings"]
    )
    assert jira.seen_jqls == ['"Parent Link" = GROUP-22223 AND issuetype = Epic']


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


@pytest.mark.anyio
async def test_stage_eval_row_includes_debug_fields() -> None:
    def fake_llm(messages):
        return {"picks": []}

    ground_truth = {
        "tickets": {
            "IDMT-19761": {
                "idmt_summary": "Prior Authorization enhancement",
                "idmt_description": "Improve PA review processing for providers.",
                "gt_by_value_stream": {
                    "Manage Utilization Management Program": ["Manage UM Operations"]
                },
            }
        }
    }

    rows = await _evaluate_ticket(
        "IDMT-19761",
        ground_truth,
        _operations_catalog(),
        fake_llm,
        None,
        SimpleNamespace(include_unverified_gt=False),
    )
    evaluated = evaluate_stage_predictions(rows)
    row = evaluated["rows"][0]

    assert row["allowed_stages"] == [
        "Manage UM Guidelines",
        "Manage Clinical Guidelines",
        "Manage UM Operations",
        "Evaluate UM Performance",
    ]
    assert row["gt_stages"] == ["Manage UM Operations"]
    assert row["predicted_stages"] == []
    assert row["false_negatives"] == ["Manage UM Operations"]
    assert "Prior Authorization enhancement" in row["idea_card_text_preview"]
    assert "prediction_warnings" in row
    assert "prediction_reasons" in row
    assert row["model_raw_response"]


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
