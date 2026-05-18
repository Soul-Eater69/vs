from __future__ import annotations

from vs_app.modules.rag.config.runtime import derive_rag_runtime_config


def test_runtime_config_for_review_pool_20() -> None:
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
