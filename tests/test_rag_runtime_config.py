from __future__ import annotations

from vs_app.modules.rag.config.runtime import derive_rag_runtime_config


RAG_PROMPT_BUDGET_ENVS = [
    "RAG_IDEA_CARD_PROMPT_CHARS",
    "RAG_CANDIDATE_DESCRIPTION_CHARS",
    "RAG_ANALOGS_PER_CANDIDATE",
    "RAG_ANALOG_CHARS",
    "RAG_HISTORICAL_TICKET_IDS_PER_CANDIDATE",
]


def clear_prompt_budget_envs(monkeypatch) -> None:
    for name in RAG_PROMPT_BUDGET_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_runtime_config_for_review_pool_20(monkeypatch) -> None:
    monkeypatch.delenv("RAG_HISTORICAL_TICKET_FETCH_K", raising=False)
    clear_prompt_budget_envs(monkeypatch)

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
    assert cfg.idea_card_prompt_chars == 1800
    assert cfg.candidate_description_chars == 100
    assert cfg.analogs_per_candidate == 2
    assert cfg.analog_chars == 80
    assert cfg.historical_ticket_ids_per_candidate == 2


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


def test_runtime_config_prompt_budget_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("RAG_IDEA_CARD_PROMPT_CHARS", "3000")
    monkeypatch.setenv("RAG_CANDIDATE_DESCRIPTION_CHARS", "220")
    monkeypatch.setenv("RAG_ANALOGS_PER_CANDIDATE", "4")
    monkeypatch.setenv("RAG_ANALOG_CHARS", "180")
    monkeypatch.setenv("RAG_HISTORICAL_TICKET_IDS_PER_CANDIDATE", "5")

    cfg = derive_rag_runtime_config(20)

    assert cfg.idea_card_prompt_chars == 3000
    assert cfg.candidate_description_chars == 220
    assert cfg.analogs_per_candidate == 4
    assert cfg.analog_chars == 180
    assert cfg.historical_ticket_ids_per_candidate == 5


def test_runtime_config_prompt_budget_env_clamps(monkeypatch) -> None:
    monkeypatch.setenv("RAG_IDEA_CARD_PROMPT_CHARS", "99999")
    monkeypatch.setenv("RAG_CANDIDATE_DESCRIPTION_CHARS", "1")
    monkeypatch.setenv("RAG_ANALOGS_PER_CANDIDATE", "99")
    monkeypatch.setenv("RAG_ANALOG_CHARS", "1")
    monkeypatch.setenv("RAG_HISTORICAL_TICKET_IDS_PER_CANDIDATE", "99")

    cfg = derive_rag_runtime_config(20)

    assert cfg.idea_card_prompt_chars == 6000
    assert cfg.candidate_description_chars == 50
    assert cfg.analogs_per_candidate == 5
    assert cfg.analog_chars == 40
    assert cfg.historical_ticket_ids_per_candidate == 5
