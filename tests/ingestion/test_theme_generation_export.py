"""Tests for the Theme-generation JSONL export (mapper + script; no Jira/Azure/LLM)."""

from __future__ import annotations

import json

import pytest

import scripts.export_theme_generation_jsonl as export_script
from vs_app.ingestion.index_documents.theme_generation_export import (
    theme_generation_documents_from_ground_truth,
)

_LINKED_THEMES = [
    {
        "theme_key": "GROUP-1",
        "business_value_stream": {"name": "Configure, Price, and Quote", "id": "VS1"},
        "business_needs_raw": "Need quoting workflow",
        "verified_stages": [
            {
                "canonical": "Account Configuration",
                "raw_mentions": [{"child_key": "EPIC-11", "source": "parent_link_child_epic_summary"}],
            },
            {
                "canonical": "Generate Quote and Present to Customer",
                "raw_mentions": [{"linked_issue_key": "EPIC-12", "source": "theme_issue_link_epic_summary"}],
            },
        ],
    },
    {
        "theme_key": "GROUP-2",
        "business_value_stream": {"name": "Manage Leads and opportunities", "id": "VS2"},
        "business_needs_raw": "Need lead routing",
        "verified_stages": [
            {"canonical": "Perform Outreach to Leads and Prospects", "raw_mentions": []}
        ],
    },
]


def _map(**overrides):
    kwargs = dict(
        ticket_id="IDMT-1",
        idmt_summary="Quoting summary",
        idmt_description="IDMT description",
        linked_themes=_LINKED_THEMES,
    )
    kwargs.update(overrides)
    return theme_generation_documents_from_ground_truth(**kwargs)


def test_mapper_builds_one_idmt_plus_theme_docs() -> None:
    docs = _map()
    assert docs[0]["id"] == "idmt::IDMT-1"
    assert [d["id"] for d in docs[1:]] == ["theme::IDMT-1::GROUP-1", "theme::IDMT-1::GROUP-2"]


def test_idmt_doc_has_all_value_streams() -> None:
    vs = _map()[0]["properties"]["value_streams"]
    assert [(r["group_id"], r["value_stream_id"], r["value_stream_name"]) for r in vs] == [
        ("GROUP-1", "VS1", "Configure, Price, and Quote"),
        ("GROUP-2", "VS2", "Manage Leads and opportunities"),
    ]


def test_theme_doc_business_needs_and_blank_description() -> None:
    theme_g1 = _map()[1]["properties"]
    assert theme_g1["business_needs"] == "Need quoting workflow"
    assert theme_g1["theme_description"] == ""


def test_stages_have_name_and_epic_id_from_mentions() -> None:
    stages = _map()[1]["properties"]["stages"]
    assert [(s["stage_name"], s["epic_id"]) for s in stages] == [
        ("Account Configuration", "EPIC-11"),
        ("Generate Quote and Present to Customer", "EPIC-12"),
    ]


def test_stage_with_no_mentions_has_blank_epic_id() -> None:
    stage = _map()[2]["properties"]["stages"][0]
    assert stage["stage_name"] == "Perform Outreach to Leads and Prospects"
    assert stage["epic_id"] == ""


def test_value_stream_support_maps_direct_implied_reason_blank_evidence() -> None:
    vs_support = [
        {"jira_group_id": "GROUP-1", "vs_name": "Configure, Price, and Quote", "inference_type": "direct", "reason": "central"},
        {"jira_group_id": "GROUP-2", "vs_name": "Manage Leads and opportunities", "inference_type": "implied", "reason": "adjacent"},
    ]
    docs = _map(value_stream_support=vs_support)
    vs = docs[0]["properties"]["value_streams"]
    assert (vs[0]["support_type"], vs[0]["reason"], vs[0]["evidence"]) == ("direct", "central", "")
    assert (vs[1]["support_type"], vs[1]["reason"], vs[1]["evidence"]) == ("implied", "adjacent", "")
    # theme doc carries its one matching VS row
    assert docs[1]["properties"]["value_streams"][0]["support_type"] == "direct"


def test_stage_support_maps_direct_reason_evidence() -> None:
    stage_support = [
        {
            "value_stream_name": "Configure, Price, and Quote",
            "stage_name": "Account Configuration",
            "support_type": "direct",
            "reason": "explicitly described",
            "evidence": "set up the account",
        }
    ]
    stage = _map(stage_support=stage_support)[1]["properties"]["stages"][0]
    assert stage["support_type"] == "direct"
    assert stage["reason"] == "explicitly described"
    assert stage["evidence"] == "set up the account"


def test_weak_broad_and_not_in_context_stage_support_become_blank() -> None:
    stage_support = [
        {"value_stream_name": "Configure, Price, and Quote", "stage_name": "Account Configuration", "support_type": "weak_broad", "reason": "r", "evidence": "e"},
        {"value_stream_name": "Configure, Price, and Quote", "stage_name": "Generate Quote and Present to Customer", "support_type": "not_in_context", "reason": "r2", "evidence": "e2"},
    ]
    stages = {s["stage_name"]: s for s in _map(stage_support=stage_support)[1]["properties"]["stages"]}
    assert stages["Account Configuration"]["support_type"] == ""
    assert stages["Generate Quote and Present to Customer"]["support_type"] == ""


def test_missing_support_produces_blank_support_fields() -> None:
    docs = _map()
    vs = docs[0]["properties"]["value_streams"][0]
    assert (vs["support_type"], vs["reason"], vs["evidence"]) == ("", "", "")
    stage = docs[1]["properties"]["stages"][0]
    assert (stage["support_type"], stage["reason"], stage["evidence"]) == ("", "", "")


def test_stage_id_blank_unless_directly_provided() -> None:
    stage_support = [
        {"value_stream_name": "Configure, Price, and Quote", "stage_name": "Account Configuration", "support_type": "direct", "stage_id": "STG-9"},
        {"value_stream_name": "Configure, Price, and Quote", "stage_name": "Generate Quote and Present to Customer", "support_type": "direct"},
    ]
    stages = {s["stage_name"]: s for s in _map(stage_support=stage_support)[1]["properties"]["stages"]}
    assert stages["Account Configuration"]["stage_id"] == "STG-9"
    assert stages["Generate Quote and Present to Customer"]["stage_id"] == ""


def test_idmt_optional_taxonomy_fields_pass_through() -> None:
    docs = _map(key_terms=["quote"], stakeholders=["Sales"], systems_and_products=["CPQ"])
    props = docs[0]["properties"]
    assert props["key_terms"] == ["quote"]
    assert props["stakeholders"] == ["Sales"]
    assert props["systems_and_products"] == ["CPQ"]


# --- export script (offline, JSONL-only) ---

def _gt_payload() -> dict:
    return {
        "tickets": {
            "IDMT-1": {
                "idmt_key": "IDMT-1",
                "idmt_summary": "Quoting summary",
                "idmt_description": "IDMT description",
                "linked_themes": _LINKED_THEMES,
            }
        }
    }


def test_script_payload_mapping_round_trip(tmp_path) -> None:
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(_gt_payload()), encoding="utf-8")
    out_path = tmp_path / "out.jsonl"

    rc = export_script.main(["--gt-input", str(gt_path), "--out", str(out_path)])
    assert rc == 0

    lines = [json.loads(line) for line in out_path.read_text().splitlines() if line.strip()]
    assert [d["document_type"] for d in lines] == ["idmt", "theme", "theme"]
    assert lines[0]["id"] == "idmt::IDMT-1"


def test_script_dry_run_writes_nothing(tmp_path) -> None:
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(_gt_payload()), encoding="utf-8")
    out_path = tmp_path / "out.jsonl"

    rc = export_script.main(["--gt-input", str(gt_path), "--out", str(out_path), "--dry-run"])
    assert rc == 0
    assert not out_path.exists()


def test_script_flags_off_by_default_make_no_llm_calls(tmp_path, monkeypatch) -> None:
    # There is no LLM import to call; assert the reserved flags default False and
    # the run completes without constructing any client.
    parser = export_script.build_arg_parser()
    args = parser.parse_args(["--gt-input", "x"])
    assert args.classify_support is False
    assert args.summary_enrich is False


def _write(path, payload) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_support_input_maps_stage_support(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    support = _write(
        tmp_path / "support.json",
        {
            "IDMT-1": {
                "stage_support": [
                    {
                        "value_stream_name": "Configure, Price, and Quote",
                        "stage_name": "Account Configuration",
                        "support_type": "direct",
                        "reason": "explicit",
                        "evidence": "set up account",
                    }
                ]
            }
        },
    )
    out = tmp_path / "out.jsonl"
    assert export_script.main(["--gt-input", gt, "--support-input", support, "--out", str(out)]) == 0
    docs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    theme_g1 = next(d for d in docs if d["id"] == "theme::IDMT-1::GROUP-1")
    stage = next(s for s in theme_g1["properties"]["stages"] if s["stage_name"] == "Account Configuration")
    assert stage["support_type"] == "direct"
    assert stage["evidence"] == "set up account"


def test_support_input_maps_value_stream_support_list_shape(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    # Shape B: list of rows with ticket_id.
    support = _write(
        tmp_path / "support.json",
        [
            {
                "ticket_id": "IDMT-1",
                "value_stream_support": [
                    {"jira_group_id": "GROUP-1", "inference_type": "implied", "reason": "adjacent"}
                ],
            }
        ],
    )
    out = tmp_path / "out.jsonl"
    assert export_script.main(["--gt-input", gt, "--support-input", support, "--out", str(out)]) == 0
    docs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    idmt = next(d for d in docs if d["document_type"] == "idmt")
    vs_g1 = next(v for v in idmt["properties"]["value_streams"] if v["group_id"] == "GROUP-1")
    assert vs_g1["support_type"] == "implied"
    assert vs_g1["reason"] == "adjacent"
    assert vs_g1["evidence"] == ""


def test_summary_input_fills_taxonomy_fields(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    summary = _write(
        tmp_path / "summary.json",
        {"IDMT-1": {"key_terms": ["quote"], "stakeholders": ["Sales"], "systems_and_products": ["CPQ"]}},
    )
    out = tmp_path / "out.jsonl"
    assert export_script.main(["--gt-input", gt, "--summary-input", summary, "--out", str(out)]) == 0
    docs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    props = next(d for d in docs if d["document_type"] == "idmt")["properties"]
    assert props["key_terms"] == ["quote"]
    assert props["stakeholders"] == ["Sales"]
    assert props["systems_and_products"] == ["CPQ"]


def test_missing_ticket_in_inputs_produces_blanks_no_failure(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    support = _write(tmp_path / "support.json", {"IDMT-OTHER": {"stage_support": []}})
    summary = _write(tmp_path / "summary.json", {"IDMT-OTHER": {"key_terms": ["x"]}})
    out = tmp_path / "out.jsonl"
    rc = export_script.main(
        ["--gt-input", gt, "--support-input", support, "--summary-input", summary, "--out", str(out)]
    )
    assert rc == 0
    docs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    idmt = next(d for d in docs if d["document_type"] == "idmt")
    assert idmt["properties"]["key_terms"] == []
    assert all(v["support_type"] == "" for v in idmt["properties"]["value_streams"])


def test_feature_8b_like_row_with_stage_support_accepted(tmp_path) -> None:
    # Shape C: Feature 8B-like dataset rows (ticket_id + stage_support, no VS support).
    gt = _write(tmp_path / "gt.json", _gt_payload())
    support = _write(
        tmp_path / "support.json",
        [
            {
                "ticket_id": "IDMT-1",
                "stage_support": [
                    {
                        "value_stream_name": "Configure, Price, and Quote",
                        "stage_name": "Account Configuration",
                        "support_type": "implied",
                        "reason": "needed",
                    }
                ],
            }
        ],
    )
    out = tmp_path / "out.jsonl"
    assert export_script.main(["--gt-input", gt, "--support-input", support, "--out", str(out)]) == 0
    docs = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]
    theme_g1 = next(d for d in docs if d["id"] == "theme::IDMT-1::GROUP-1")
    stage = next(s for s in theme_g1["properties"]["stages"] if s["stage_name"] == "Account Configuration")
    assert stage["support_type"] == "implied"


def test_malformed_support_row_skipped_without_failing(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    # One bad entry (not a dict) alongside a good one.
    support = _write(tmp_path / "support.json", ["not-a-dict", {"ticket_id": "IDMT-1", "stage_support": []}])
    out = tmp_path / "out.jsonl"
    assert export_script.main(["--gt-input", gt, "--support-input", support, "--out", str(out)]) == 0
    assert out.exists()


def test_invalid_support_path_fails_clearly(tmp_path) -> None:
    gt = _write(tmp_path / "gt.json", _gt_payload())
    out = tmp_path / "out.jsonl"
    with pytest.raises(FileNotFoundError):
        export_script.main(
            ["--gt-input", gt, "--support-input", str(tmp_path / "missing.json"), "--out", str(out)]
        )


def test_reserved_flags_warn_and_produce_identical_output(tmp_path, capsys) -> None:
    gt_path = tmp_path / "gt.json"
    gt_path.write_text(json.dumps(_gt_payload()), encoding="utf-8")
    plain_out = tmp_path / "plain.jsonl"
    flagged_out = tmp_path / "flagged.jsonl"

    rc1 = export_script.main(["--gt-input", str(gt_path), "--out", str(plain_out)])
    capsys.readouterr()  # clear
    rc2 = export_script.main(
        [
            "--gt-input",
            str(gt_path),
            "--out",
            str(flagged_out),
            "--classify-support",
            "--summary-enrich",
        ]
    )
    out = capsys.readouterr().out

    assert rc1 == 0 and rc2 == 0
    # Reserved-flag run emits the warning...
    assert export_script.RESERVED_FLAG_WARNING in out
    # ...and produces byte-identical JSONL (no enrichment ran).
    assert flagged_out.read_text() == plain_out.read_text()
