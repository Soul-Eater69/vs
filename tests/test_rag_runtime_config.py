from __future__ import annotations

from vs_app.modules.rag.config.runtime import derive_rag_runtime_config


def test_runtime_config_for_review_pool_20(monkeypatch) -> None:
    monkeypatch.delenv("RAG_HISTORICAL_TICKET_FETCH_K", raising=False)

    cfg = derive_rag_runtime_config(20)

    assert cfg.final_output_count == 20
    assert cfg.semantic_fetch_k == 60
    assert cfg.historical_ticket_fetch_k == 60
    assert not hasattr(cfg, "historical_evidence_top_k")
    assert not hasattr(cfg, "min_historical_evidence_score")
    assert cfg.llm_candidate_window == 50
    assert cfg.max_semantic_plus_historical == 50
    assert cfg.max_semantic_only == 1
    assert cfg.max_historical_only == 8


def test_runtime_config_historical_fetch_k_env_override(monkeypatch) -> None:
    monkeypatch.setenv("RAG_HISTORICAL_TICKET_FETCH_K", "6")

    cfg = derive_rag_runtime_config(20)

    assert cfg.historical_ticket_fetch_k == 6


def test_runtime_config_historical_fetch_k_invalid_env_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("RAG_HISTORICAL_TICKET_FETCH_K", "not-a-number")

    cfg = derive_rag_runtime_config(20)

    assert cfg.historical_ticket_fetch_k == 60


def test_runtime_config_historical_fetch_k_env_clamps_high(monkeypatch) -> None:
    monkeypatch.setenv("RAG_HISTORICAL_TICKET_FETCH_K", "500")

    cfg = derive_rag_runtime_config(20)

    assert cfg.historical_ticket_fetch_k == 100
