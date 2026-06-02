"""Tests for the manual Theme-generation CLI (Feature 15B; fakes only)."""

from __future__ import annotations

import json

import pytest

import scripts.run_theme_generation as cli

# NOTE: --value-streams is comma-separated, so value stream names used here must
# not themselves contain commas (see the runbook's comma caveat).
VS = "Manage Utilization Management Program"
VS2 = "Manage Leads and opportunities"
CATALOG = {
    VS: {"value_stream_id": "VS-UM", "stages": [{"name": "Manage UM Operations"}, {"name": "Evaluate UM Performance"}]},
    VS2: {"stages": [{"name": "Perform Outreach to Leads and Prospects"}]},
}


def _tripwire_factories(monkeypatch):
    """Make every live-client factory blow up if called (dry-run must not call them)."""
    def boom(*_a, **_k):
        raise AssertionError("dry-run must not construct live clients")

    monkeypatch.setattr(cli, "make_embedding_client", boom)
    monkeypatch.setattr(cli, "make_generation_service", boom)
    monkeypatch.setattr(cli, "make_theme_generation_search_adapter", boom)


def _patch_catalog(monkeypatch, catalog=CATALOG):
    monkeypatch.setattr(cli, "load_stage_catalog", lambda *, path, source: catalog)


# --- dry-run ----------------------------------------------------------------


def test_dry_run_constructs_no_clients_and_prints_plan(monkeypatch, capsys) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    rc = cli.main(["--idea", "Sales need faster quoting", "--value-streams", f"{VS}, {VS2}"])
    assert rc == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "dry-run"
    assert plan["skipped"] is True
    assert plan["selected_value_streams"] == [VS, VS2]
    assert plan["allowed_stage_counts"] == {VS: 2, VS2: 1}
    assert plan["top_k_idmt"] == 15 and plan["max_examples"] == 5
    assert plan["idea_length"] == len("Sales need faster quoting")
    assert plan["would_run"] == ["embedding", "azure_read_search", "llm"]


def test_dry_run_uses_index_name_override(monkeypatch, capsys) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    cli.main(["--idea", "x", "--value-streams", VS, "--index-name", "my-poc-index"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["resolved_index_name"] == "my-poc-index"


def test_dry_run_tolerates_missing_catalog(monkeypatch, capsys) -> None:
    _tripwire_factories(monkeypatch)

    def boom(*, path, source):
        raise FileNotFoundError(path)

    monkeypatch.setattr(cli, "load_stage_catalog", boom)
    cli.main(["--idea", "x", "--value-streams", VS])
    plan = json.loads(capsys.readouterr().out)
    assert plan["allowed_stage_counts"] == {VS: 0}
    assert any("stage catalog could not be loaded" in w for w in plan["warnings"])


# --- --run (fakes only) -----------------------------------------------------


class FakeEmbedder:
    def __init__(self) -> None:
        self.embedded: list[str] = []

    def embed(self, text):
        self.embedded.append(text)
        return [0.1, 0.2, 0.3]


def _patch_run_factories(monkeypatch, embedder, adapter, llm, captured=None):
    monkeypatch.setattr(cli, "make_embedding_client", lambda: embedder)
    monkeypatch.setattr(cli, "make_theme_generation_search_adapter", lambda index_name: adapter)
    monkeypatch.setattr(cli, "make_generation_service", lambda: llm)

    def fake_generate(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return [{"value_stream_name": kwargs["selected_value_streams"][0], "theme_description": "td", "business_needs": "bn", "selected_stages": [], "examples_used": [], "warnings": []}]

    monkeypatch.setattr(cli, "generate_themes_for_idea", fake_generate)


def test_run_path_returns_json_result(monkeypatch, capsys) -> None:
    _patch_catalog(monkeypatch)
    embedder = FakeEmbedder()
    captured: dict = {}
    _patch_run_factories(monkeypatch, embedder, adapter=object(), llm=object(), captured=captured)

    rc = cli.main(["--idea", "Faster quoting", "--value-streams", VS, "--run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["value_stream_name"] == VS
    # idea embedded once; query_vector + adapter passed through to the orchestrator
    assert embedder.embedded == ["Faster quoting"]
    assert captured["query_vector"] == [0.1, 0.2, 0.3]
    assert captured["selected_value_streams"] == [VS]


def test_run_writes_output_file(monkeypatch, tmp_path, capsys) -> None:
    _patch_catalog(monkeypatch)
    _patch_run_factories(monkeypatch, FakeEmbedder(), adapter=object(), llm=object())
    out_path = tmp_path / "result.json"
    cli.main(["--idea", "x", "--value-streams", VS, "--run", "--output", str(out_path)])
    written = json.loads(out_path.read_text())
    assert written[0]["value_stream_name"] == VS


def test_run_index_override_reaches_adapter_factory(monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    seen: dict = {}

    def fake_adapter_factory(index_name):
        seen["index_name"] = index_name
        return object()

    monkeypatch.setattr(cli, "make_embedding_client", lambda: FakeEmbedder())
    monkeypatch.setattr(cli, "make_theme_generation_search_adapter", fake_adapter_factory)
    monkeypatch.setattr(cli, "make_generation_service", lambda: object())
    monkeypatch.setattr(cli, "generate_themes_for_idea", lambda **k: [])

    cli.main(["--idea", "x", "--value-streams", VS, "--index-name", "override-idx", "--run"])
    assert seen["index_name"] == "override-idx"


def test_run_passes_loaded_catalog_to_orchestrator(monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    captured: dict = {}
    _patch_run_factories(monkeypatch, FakeEmbedder(), adapter=object(), llm=object(), captured=captured)
    cli.main(["--idea", "x", "--value-streams", VS, "--run"])
    assert captured["stage_catalog"] == CATALOG


# --- input validation -------------------------------------------------------


def test_missing_idea_errors(monkeypatch) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["--value-streams", VS])


def test_both_idea_and_idea_file_errors(monkeypatch, tmp_path) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    f = tmp_path / "idea.txt"
    f.write_text("idea", encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["--idea", "x", "--idea-file", str(f), "--value-streams", VS])


def test_missing_value_streams_errors() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--idea", "x", "--value-streams", " , , "])


def test_idea_file_missing_path_errors(monkeypatch) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    with pytest.raises(SystemExit):
        cli.main(["--idea-file", "/nonexistent/idea.txt", "--value-streams", VS])


def test_idea_file_is_read(monkeypatch, tmp_path, capsys) -> None:
    _tripwire_factories(monkeypatch)
    _patch_catalog(monkeypatch)
    f = tmp_path / "idea.txt"
    f.write_text("idea from file", encoding="utf-8")
    cli.main(["--idea-file", str(f), "--value-streams", VS])
    plan = json.loads(capsys.readouterr().out)
    assert plan["idea_length"] == len("idea from file")


# --- import safety ----------------------------------------------------------


def test_no_jira_or_upload_imports() -> None:
    import ast

    tree = ast.parse(open(cli.__file__, encoding="utf-8").read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [a.name for a in node.names]
    blob = " ".join(imported).lower()
    for forbidden in ("jira", "upload_theme_generation", "create_theme_generation_index", "index_manager", "uploader"):
        assert forbidden not in blob, f"unexpected import referencing {forbidden}"
