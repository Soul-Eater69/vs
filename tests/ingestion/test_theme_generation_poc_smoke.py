"""End-to-end POC smoke test: GT fixture -> export JSONL -> dry-run upload.

Fakes/local only. No Azure, no Jira, no LLM. Proves the Feature 9-12 pipeline
wires together with the committed fixtures (2 IDMT tickets / 3 themes / 5 docs).
"""

from __future__ import annotations

import json
from pathlib import Path

import scripts.export_theme_generation_jsonl as export_script
from vs_app.ingestion.upload.uploader import (
    read_jsonl,
    summarize_documents,
    upload_theme_generation_documents,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "theme_generation"
GT = FIXTURES / "sample_stage_ground_truth.json"
SUPPORT = FIXTURES / "sample_support_input.json"
SUMMARY = FIXTURES / "sample_summary_input.json"


class FakeDocsClient:
    def __init__(self) -> None:
        self.calls: list = []

    def upload_documents(self, *, index_name, documents, batch_size):
        self.calls.append((index_name, documents, batch_size))
        return {"ok": True}


def _export(tmp_path, *extra) -> Path:
    out = tmp_path / "theme_gen.jsonl"
    rc = export_script.main(["--gt-input", str(GT), "--out", str(out), *extra])
    assert rc == 0
    return out


def test_export_produces_expected_documents(tmp_path) -> None:
    out = _export(tmp_path)
    assert out.exists()

    docs = read_jsonl(out)
    assert len(docs) == 5
    assert sum(d["document_type"] == "idmt" for d in docs) == 2
    assert sum(d["document_type"] == "theme" for d in docs) == 3

    ids = {d["id"] for d in docs}
    assert {"idmt::IDMT-1001", "idmt::IDMT-1002"} <= ids
    assert {
        "theme::IDMT-1001::GROUP-2001",
        "theme::IDMT-1001::GROUP-2002",
        "theme::IDMT-1002::GROUP-2003",
    } <= ids

    # Theme docs are vector-less by design.
    assert all("content_vector" not in d for d in docs if d["document_type"] == "theme")


def test_dry_run_upload_makes_no_client_calls(tmp_path) -> None:
    docs = read_jsonl(_export(tmp_path))
    fake = FakeDocsClient()

    result = upload_theme_generation_documents(
        docs=docs, dry_run=True, documents_client=fake
    )

    assert fake.calls == []
    assert result["dry_run"] is True
    assert result["uploaded"] is False
    summary = summarize_documents(docs)
    assert (summary["total"], summary["idmt"], summary["theme"], summary["with_vectors"]) == (5, 2, 3, 0)


def test_enriched_export_populates_fields_and_keeps_counts(tmp_path) -> None:
    out = _export(tmp_path, "--support-input", str(SUPPORT), "--summary-input", str(SUMMARY))
    docs = read_jsonl(out)

    # Counts unchanged by enrichment.
    assert len(docs) == 5
    assert sum(d["document_type"] == "idmt" for d in docs) == 2
    assert sum(d["document_type"] == "theme" for d in docs) == 3

    by_id = {d["id"]: d for d in docs}

    # Enriched ticket: VS support + summary fields populated.
    a = by_id["idmt::IDMT-1001"]["properties"]
    a_vs = {row["value_stream_name"]: row for row in a["value_streams"]}
    assert a_vs["Configure, Price, and Quote"]["support_type"] == "direct"
    assert a_vs["Manage Leads and opportunities"]["support_type"] == "implied"
    assert a["key_terms"] == ["quoting", "account configuration", "lead routing"]
    assert a["stakeholders"] == ["Commercial Sales", "Sales Operations"]
    assert a["systems_and_products"] == ["CPQ", "CRM"]

    # Stage support flows into the matching theme doc.
    theme_cpq = by_id["theme::IDMT-1001::GROUP-2001"]["properties"]
    acct = next(s for s in theme_cpq["stages"] if s["stage_name"] == "Account Configuration")
    assert acct["support_type"] == "direct"
    assert acct["evidence"] == "faster account configuration"

    # Ticket absent from enrichment files -> blanks / [].
    b = by_id["idmt::IDMT-1002"]["properties"]
    assert b["key_terms"] == []
    assert all(row["support_type"] == "" for row in b["value_streams"])
