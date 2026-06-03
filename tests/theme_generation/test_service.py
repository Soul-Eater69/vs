"""Fake-only tests for the theme_generation service facade.

The facade is a thin delegation to the Value Stream generator. No live
Azure / LLM / Jira: a fake RAG pipeline_fn supplies a canned payload.
"""

from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.theme_generation import service
from vs_app.theme_generation.service import (
    GeneratedValueStream,
    ValueStreamGenerationRequest,
    ValueStreamGenerationResult,
)


def _fake_rag_service() -> ValueStreamRagService:
    def pipeline_fn(query: str, **kwargs) -> dict:
        return {
            "selected_value_streams": [
                {
                    "entity_id": "VS-CPQ",
                    "entity_name": "Configure, Price, and Quote",
                    "confidence": 0.9,
                    "reason": "Quoting automation.",
                    "selection_source": "llm_pick",
                    "supporting_ticket_ids": ["IDMT-1001"],
                }
            ],
            "candidate_value_streams": [
                {
                    "entity_id": "VS-CPQ",
                    "entity_name": "Configure, Price, and Quote",
                    "from_semantic": True,
                    "from_historical": True,
                    "supporting_ticket_ids": ["IDMT-1001"],
                    "historical_reasons": ["Prior CPQ ticket."],
                }
            ],
            "historical_source": "azure",
        }

    return ValueStreamRagService(pipeline_fn=pipeline_fn)


def test_facade_returns_generation_contract() -> None:
    request = ValueStreamGenerationRequest(idea_card_text="quote idea")
    result = asyncio.run(
        service.generate_value_streams(request, rag_service=_fake_rag_service())
    )

    assert isinstance(result, ValueStreamGenerationResult)
    assert len(result.value_streams) == 1
    vs = result.value_streams[0]
    assert isinstance(vs, GeneratedValueStream)
    assert vs.name == "Configure, Price, and Quote"
    assert vs.support_type == "direct"
    assert vs.historic_idmt_ids == ["IDMT-1001"]


def test_facade_delegates_to_generator(monkeypatch) -> None:
    sentinel = ValueStreamGenerationResult(value_streams=[], warnings=["delegated"], debug={})
    seen: dict = {}

    async def fake_generate(request, *, service=None):
        seen["request"] = request
        seen["service"] = service
        return sentinel

    monkeypatch.setattr(service, "_generate", fake_generate)

    request = ValueStreamGenerationRequest(idea_card_text="x", top_n=5)
    out = asyncio.run(service.generate_value_streams(request, rag_service="RAG"))

    assert out is sentinel
    assert seen["request"] is request
    assert seen["service"] == "RAG"
