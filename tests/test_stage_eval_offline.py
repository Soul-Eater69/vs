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
    business_value_stream_from_resolved_link,
    build_theme_stage_ground_truth,
    build_ticket_stage_ground_truth,
    extract_epic_links_from_theme_issue,
    extract_stage_from_child_epic_summary,
    extract_raw_stage_mentions_from_business_needs,
    fetch_direct_child_epics,
    generate_stage_candidates_from_child_epic_summary,
    parse_business_value_stream,
    find_linked_theme_issues,
    resolve_child_epic_stage,
    resolve_linked_epic_stage,
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


def test_business_value_stream_from_resolved_link_uses_jira_label_mapping() -> None:
    parsed = business_value_stream_from_resolved_link(
        {
            "id": "VSR00168130",
            "jira_group_id": "GROUP-22223",
            "name": "Manage Utilization Management Program",
            "summary_raw": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            "source": "jira_implemented_by_group_links",
        },
        theme_summary="Fallback : Ignore Me",
    )

    assert parsed == {
        "raw": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
        "name": "Manage Utilization Management Program",
        "id": "VSR00168130",
        "source": "jira_implemented_by_group_links",
    }


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

    themes = find_linked_theme_issues(issue)
    assert len(themes) == 1
    assert themes[0]["key"] == "GROUP-22223"
    assert themes[0]["summary_raw"] == "Theme summary"
    assert themes[0]["issue_type"] == "Theme"


def test_business_needs_does_not_create_stage_ground_truth() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
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


def test_extract_epic_links_from_theme_issue_reads_implemented_by_epics() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            "issuelinks": [
                {
                    "type": {"name": "implements", "outward": "is implemented by"},
                    "outwardIssue": {
                        "key": "GROUP-22805",
                        "fields": {
                            "summary": (
                                "CP 2026 Women's and Family Health : "
                                "Manage Utilization Management Program - Manage UM Operations (PA)"
                            ),
                            "issuetype": {"name": "Epic"},
                            "status": {"name": "In Progress"},
                        },
                    },
                },
                {
                    "type": {"name": "relates"},
                    "outwardIssue": {
                        "key": "GROUP-99999",
                        "fields": {
                            "summary": "Not an implementation stage",
                            "issuetype": {"name": "Epic"},
                        },
                    },
                },
            ],
        },
    }

    rows = extract_epic_links_from_theme_issue(theme)

    assert rows == [
        {
            "key": "GROUP-22805",
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (PA)"
            ),
            "status": "In Progress",
            "issue_type": "Epic",
            "link_direction": "outwardIssue",
            "link_type": "implements | is implemented by",
        }
    ]


def test_resolve_linked_epic_stage_reuses_child_epic_summary_logic() -> None:
    resolution = resolve_linked_epic_stage(
        linked_issue={
            "key": "GROUP-22805",
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (PA)"
            ),
        },
        allowed_stages=_operations_catalog()["Manage Utilization Management Program"]["stages"],
        value_stream_name="Manage Utilization Management Program",
    )

    assert resolution["linked_issue_key"] == "GROUP-22805"
    assert resolution["included"] is True
    assert resolution["canonical_stage"] == "Manage UM Operations"


def test_theme_issue_link_epic_summary_creates_stage_ground_truth_without_parent_link_children() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            BUSINESS_NEEDS_FIELD: "",
            "issuelinks": [
                {
                    "type": {"name": "implements", "outward": "is implemented by"},
                    "outwardIssue": {
                        "key": "GROUP-22805",
                        "fields": {
                            "summary": (
                                "CP 2026 Women's and Family Health : "
                                "Manage Utilization Management Program - Manage UM Operations (PA)"
                            ),
                            "issuetype": {"name": "Epic"},
                            "status": {"name": "In Progress"},
                        },
                    },
                }
            ],
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_operations_catalog(),
        child_epics=[],
        child_lookup_debug={},
        resolved_value_stream={
            "name": "Manage Utilization Management Program",
            "id": "VSR00168130",
            "summary_raw": theme["fields"]["summary"],
            "source": "test",
        },
    )

    assert [stage["canonical"] for stage in result["verified_stages"]] == [
        "Manage UM Operations"
    ]
    assert result["linked_epic_mentions_debug"][0]["source"] == "theme_issue_link_epic_summary"
    assert result["linked_epic_mentions_debug"][0]["linked_issue_key"] == "GROUP-22805"
    assert result["verified_stages"][0]["raw_mentions"][0]["source"] == "theme_issue_link_epic_summary"


def test_theme_link_and_parent_link_duplicate_stage_preserves_both_mentions() -> None:
    theme = {
        "key": "GROUP-22223",
        "fields": {
            "summary": "CP 2026 Women's and Family Health : Manage Utilization Management Program",
            BUSINESS_NEEDS_FIELD: "",
            "issuelinks": [
                {
                    "type": {"name": "implements", "outward": "is implemented by"},
                    "outwardIssue": {
                        "key": "GROUP-22805",
                        "fields": {
                            "summary": (
                                "CP 2026 Women's and Family Health : "
                                "Manage Utilization Management Program - Manage UM Operations (PA)"
                            ),
                            "issuetype": {"name": "Epic"},
                        },
                    },
                }
            ],
        },
    }
    child = {
        "key": "GROUP-22838",
        "fields": {
            "summary": (
                "CP 2026 Women's and Family Health : "
                "Manage Utilization Management Program - Manage UM Operations (Referral)"
            ),
            "issuetype": {"name": "Epic"},
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog=_operations_catalog(),
        child_epics=[child],
        child_lookup_debug={},
        resolved_value_stream={
            "name": "Manage Utilization Management Program",
            "id": "VSR00168130",
            "summary_raw": theme["fields"]["summary"],
            "source": "test",
        },
    )

    assert [stage["canonical"] for stage in result["verified_stages"]] == [
        "Manage UM Operations"
    ]
    raw_mentions = result["verified_stages"][0]["raw_mentions"]
    assert [mention["source"] for mention in raw_mentions] == [
        "theme_issue_link_epic_summary",
        "parent_link_child_epic_summary",
    ]


def test_theme_group_summary_alone_does_not_create_stage_ground_truth() -> None:
    theme = {
        "key": "GROUP-18562",
        "fields": {
            "summary": "FEP 2023 I00015424 FEP PSHBP - Establish Product Offering",
            BUSINESS_NEEDS_FIELD: "",
            "issuelinks": [],
        },
    }

    result = build_theme_stage_ground_truth(
        theme_issue=theme,
        catalog={
            "Manage Product Offering": {
                "stages": [
                    {"name": "Establish Product Offering", "id": "", "description": "", "aliases": []}
                ]
            }
        },
        child_epics=[],
        child_lookup_debug={},
        resolved_value_stream={
            "name": "Manage Product Offering",
            "id": "VSR-PRODUCT",
            "summary_raw": theme["fields"]["summary"],
            "source": "test",
        },
    )

    assert result["linked_epic_rows"] == []
    assert result["verified_stages"] == []


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
            "source": "parent_link_child_epic_summary",
            "source_text": child["fields"]["summary"],
            "child_key": "GROUP-22722",
            "theme_key": "GROUP-22219",
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
                            "summary": (
                                "CP 2026 Women's and Family Health : "
                                "Manage Utilization Management Program"
                            ),
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
    assert result["value_stream_resolution"]["linked_value_streams"][0]["jira_group_id"] == "GROUP-22223"
    theme_gt = result["linked_themes"][0]
    assert theme_gt["business_value_stream"]["source"] == "jira_implemented_by_group_links"
    assert theme_gt["business_value_stream"]["raw"].endswith("Manage Utilization Management Program")


@pytest.mark.anyio
async def test_ground_truth_builder_uses_theme_issue_link_epics_when_no_parent_link_children() -> None:
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
                            "summary": (
                                "CP 2026 Women's and Family Health - "
                                "Manage Utilization Management Program"
                            ),
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
            "summary": (
                "CP 2026 Women's and Family Health - "
                "Manage Utilization Management Program"
            ),
            "issuetype": {"name": "Theme"},
            BUSINESS_NEEDS_FIELD: "",
            "subtasks": [],
            "issuelinks": [
                {
                    "type": {"name": "implements", "outward": "is implemented by"},
                    "outwardIssue": {
                        "key": "GROUP-22805",
                        "fields": {
                            "summary": (
                                "CP 2026 Women's and Family Health - "
                                "Manage Utilization Management Program - Manage UM Operations (PA)"
                            ),
                            "issuetype": {"name": "Epic"},
                            "status": {"name": "In Progress"},
                        },
                    },
                }
            ],
        },
    }

    result = await build_ticket_stage_ground_truth(
        ticket_key="IDMT-19761",
        jira_client=_SearchFakeJira(
            {"IDMT-19761": idmt, "GROUP-22223": theme},
            search_results={'"Parent Link" = GROUP-22223 AND issuetype = Epic': []},
        ),
        catalog=_operations_catalog(),
    )

    assert result["gt_by_value_stream"] == {
        "Manage Utilization Management Program": ["Manage UM Operations"]
    }
    theme_gt = result["linked_themes"][0]
    assert theme_gt["verified_stages"][0]["raw_mentions"][0]["source"] == "theme_issue_link_epic_summary"
    assert theme_gt["linked_epic_rows"][0]["key"] == "GROUP-22805"


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
    assert lookup["lookup_strategy"] == "parent_link_and_parent"
    assert lookup["jql_attempts"][0]["jql"] == '"Parent Link" = GROUP-22223 AND issuetype = Epic'
    assert lookup["jql_attempts"][0]["count"] == 2
    assert lookup["jql_attempts"][1]["jql"] == "parent = GROUP-22223 AND issuetype = Epic"
    assert lookup["jql_attempts"][1]["count"] == 0
    assert lookup["child_issue_sources"] == {
        "GROUP-22805": ["parent_link"],
        "GROUP-22838": ["parent_link"],
    }
    assert jira.seen_jqls == [
        '"Parent Link" = GROUP-22223 AND issuetype = Epic',
        "parent = GROUP-22223 AND issuetype = Epic",
    ]


def _epic(key: str, summary: str) -> dict:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": "In Progress"},
            "issuetype": {"name": "Epic"},
        },
    }


@pytest.mark.anyio
async def test_fetch_direct_child_epics_parent_relation_only() -> None:
    epic_a = _epic("GROUP-30", "VS - Stage A")
    epic_b = _epic("GROUP-31", "VS - Stage B")
    jira = _SearchFakeJira(
        {},
        search_results={"parent = GROUP-1 AND issuetype = Epic": [epic_a, epic_b]},
    )

    child_issues, lookup = await fetch_direct_child_epics(jira_client=jira, theme_key="GROUP-1")

    assert [issue["key"] for issue in child_issues] == ["GROUP-30", "GROUP-31"]
    assert lookup["child_issue_keys"] == ["GROUP-30", "GROUP-31"]
    assert lookup["jql_attempts"][0]["count"] == 0
    assert lookup["jql_attempts"][1]["count"] == 2
    assert lookup["child_issue_sources"] == {
        "GROUP-30": ["parent"],
        "GROUP-31": ["parent"],
    }
    assert jira.seen_jqls == [
        '"Parent Link" = GROUP-1 AND issuetype = Epic',
        "parent = GROUP-1 AND issuetype = Epic",
    ]


@pytest.mark.anyio
async def test_fetch_direct_child_epics_merges_and_dedupes_both_sources() -> None:
    shared = _epic("GROUP-10", "VS - Shared")
    parent_link_only = _epic("GROUP-11", "VS - PL Only")
    parent_only = _epic("GROUP-12", "VS - Parent Only")
    jira = _SearchFakeJira(
        {},
        search_results={
            '"Parent Link" = GROUP-1 AND issuetype = Epic': [shared, parent_link_only],
            "parent = GROUP-1 AND issuetype = Epic": [shared, parent_only],
        },
    )

    child_issues, lookup = await fetch_direct_child_epics(jira_client=jira, theme_key="GROUP-1")

    assert [issue["key"] for issue in child_issues] == ["GROUP-10", "GROUP-11", "GROUP-12"]
    assert lookup["child_issue_keys"] == ["GROUP-10", "GROUP-11", "GROUP-12"]
    assert lookup["child_issue_sources"] == {
        "GROUP-10": ["parent_link", "parent"],
        "GROUP-11": ["parent_link"],
        "GROUP-12": ["parent"],
    }


@pytest.mark.anyio
async def test_fetch_direct_child_epics_no_children() -> None:
    jira = _SearchFakeJira({}, search_results={})

    child_issues, lookup = await fetch_direct_child_epics(jira_client=jira, theme_key="GROUP-1")

    assert child_issues == []
    assert lookup["child_issue_keys"] == []
    assert lookup["child_issue_sources"] == {}
    assert (
        "no direct child Epics found via Parent Link or parent relation"
        in lookup["warnings"]
    )


@pytest.mark.anyio
async def test_fetch_direct_child_epics_parent_failure_preserves_parent_link() -> None:
    epic = _epic("GROUP-40", "VS - Stage")
    parent_jql = "parent = GROUP-1 AND issuetype = Epic"
    jira = _SearchFakeJira(
        {},
        search_results={'"Parent Link" = GROUP-1 AND issuetype = Epic': [epic]},
        error_jqls={parent_jql},
    )

    child_issues, lookup = await fetch_direct_child_epics(jira_client=jira, theme_key="GROUP-1")

    assert [issue["key"] for issue in child_issues] == ["GROUP-40"]
    assert lookup["child_issue_keys"] == ["GROUP-40"]
    assert lookup["jql_attempts"][1]["error"]
    assert "Jira parent-relation child Epic lookup failed" in lookup["warnings"]
    assert lookup["child_issue_sources"] == {"GROUP-40": ["parent_link"]}


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
                            "summary": (
                                "CP 2026 Women's and Family Health : "
                                "Manage Utilization Management Program"
                            ),
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
    assert result["gt_by_value_stream"] == {}
    assert any(
        "no direct child Epics found via Parent Link or parent relation" in warning
        for warning in theme_gt["warnings"]
    )
    assert jira.seen_jqls == [
        '"Parent Link" = GROUP-22223 AND issuetype = Epic',
        "parent = GROUP-22223 AND issuetype = Epic",
    ]


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
