from __future__ import annotations

from vs_app.modules.rag.augmentation.finalizer import _split_llm_candidates
from vs_app.modules.rag.augmentation.prompt_builder import (
    build_final_consolidation_prompt,
    build_historical_gap_prompt,
)


def test_split_llm_candidates_separates_historical_gap_lane() -> None:
    direct = {"entity_name": "Configure, Price, and Quote", "candidate_lane": "confirmed_direct"}
    semantic = {"entity_name": "Establish Product Offering", "candidate_lane": "semantic_direct"}
    historical = {"entity_name": "Issue Payment", "candidate_lane": "historical_recall"}

    direct_candidates, historical_candidates = _split_llm_candidates([direct, historical, semantic])

    assert direct_candidates == [direct, semantic]
    assert historical_candidates == [historical]


def test_historical_gap_prompt_explains_pattern_induced_selection() -> None:
    prompt = build_historical_gap_prompt(
        query_for_prompt="Roadmap to design and deliver tiered network and steering capabilities.",
        candidates=[
            {
                "entity_id": "vs-invoice",
                "entity_name": "Manage Invoice and Payment Receipt",
                "bucket": "historical_only",
                "candidate_lane": "historical_recall",
                "from_historical": True,
                "support_count": 10,
                "direct_count": 0,
                "implied_count": 10,
                "best_support_score": 0.867,
                "avg_support_score": 0.62,
                "historical_reasons": [
                    "[IDMT-1 / implied] recurring billing and payment receipt analog"
                ],
            },
            {
                "entity_id": "vs-member",
                "entity_name": "Manage Member Care",
                "bucket": "historical_only",
                "candidate_lane": "historical_recall",
                "from_historical": True,
                "support_count": 9,
                "direct_count": 1,
                "implied_count": 8,
                "best_support_score": 0.714,
                "avg_support_score": 0.60,
                "historical_reasons": [
                    "[IDMT-1 / direct] recurring billing and payment receipt analog"
                ],
            }
        ],
    )

    assert "separate historical pattern adjudication pass" in prompt
    assert "pattern-induced" in prompt
    assert "SIMILAR HISTORICAL PRECEDENTS" in prompt
    assert "Pattern-supported value streams: direct: Manage Member Care; implied: Manage Invoice and Payment Receipt" in prompt
    assert "Do not include \"Align and Execute IT Strategy\"" in prompt
    assert "Manage Invoice and Payment Receipt" in prompt
    assert "Analog evidence" in prompt


def test_historical_gap_prompt_includes_top_faiss_ticket_context() -> None:
    prompt = build_historical_gap_prompt(
        query_for_prompt="Roadmap to design and deliver tiered network and steering capabilities.",
        candidates=[
            {
                "entity_id": "vs-member",
                "entity_name": "Manage Member Care",
                "bucket": "historical_only",
                "candidate_lane": "historical_recall",
                "from_historical": True,
                "support_count": 9,
                "direct_count": 1,
                "implied_count": 8,
                "best_support_score": 0.714,
                "avg_support_score": 0.60,
            }
        ],
        historical_ticket_hits=[
            {
                "ticket_id": "IDMT-11455",
                "title": "Tiered network steering roadmap",
                "best_score": 0.726,
                "summary_preview": "This initiative proposes a roadmap to design and deliver tiered network and steerage capabilities.",
                "direct_vs_names": ["Manage Member Care"],
                "implied_vs_names": ["Establish Provider Network", "Ensure Payment Integrity"],
                "label_source": "jira_issuelinks",
            }
        ],
    )

    assert "TOP RETRIEVED HISTORICAL TICKETS" in prompt
    assert "IDMT-11455 - Tiered network steering roadmap" in prompt
    assert "Direct value streams: Manage Member Care" in prompt
    assert "Implied value streams: Establish Provider Network, Ensure Payment Integrity" in prompt
    assert "Ticket summary: This initiative proposes a roadmap" in prompt


def test_final_consolidation_prompt_keeps_passes_advisory_not_union() -> None:
    prompt = build_final_consolidation_prompt(
        query_for_prompt="Launch a tiered benefit product with price transparency and provider steering.",
        candidate_options=[
            {
                "entity_id": "vs-cpq",
                "entity_name": "Configure, Price, and Quote",
                "candidate_lane": "confirmed_direct",
                "from_semantic": True,
                "description": "Determining products and prices.",
            },
            {
                "entity_id": "vs-member",
                "entity_name": "Manage Member Care",
                "candidate_lane": "historical_recall",
                "from_historical": True,
                "support_count": 4,
                "direct_count": 1,
                "implied_count": 3,
                "description": "Helping members navigate care.",
                "historical_reasons": ["[IDMT-1 / implied] member navigation recurred in tiering work"],
            },
        ],
        direct_selected=[
            {
                "entity_id": "vs-cpq",
                "entity_name": "Configure, Price, and Quote",
                "confidence": 0.9,
                "reason": "The product needs pricing and quoting.",
            }
        ],
        historical_selected=[
            {
                "entity_id": "vs-member",
                "entity_name": "Manage Member Care",
                "confidence": 0.85,
                "reason": "Similar tiering tickets required member navigation.",
            }
        ],
    )

    assert "final value-stream adjudicator" in prompt
    assert "Do not mechanically union" in prompt
    assert "DIRECT LLM PROPOSALS" in prompt
    assert "HISTORIC LLM PROPOSALS" in prompt
    assert "ALLOWED VALUE-STREAM OPTIONS" in prompt
    assert "member navigation recurred" in prompt
