"""RAG / impact-analysis service facade.

Orchestrates: query view building -> parallel retrieval (semantic + historical)
-> candidate merging -> ranking -> optional LLM finalizer -> product impact
resolution. Delegates each stage to injected components.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(slots=True)
class ImpactAnalysisCommand:
    ticket_id: str | None = None
    idea_card_text: str | None = None
    source: Literal["jira", "neo4j"] | None = None
    top_k_historical: int = 20
    top_k_value_streams: int = 20
    use_llm_finalizer: bool = True


@dataclass(slots=True)
class ImpactAnalysisResult:
    predicted_value_streams: list[dict]
    impacted_products: list[dict]
    evidence: list[dict]
    debug: dict


class RagImpactService:
    def __init__(
        self,
        query_builder: Any,
        value_stream_retriever: Any,
        historical_retriever: Any,
        candidate_merger: Any,
        ranker: Any,
        finalizer: Any,
        product_impact_service: Any,
    ) -> None:
        self.query_builder = query_builder
        self.value_stream_retriever = value_stream_retriever
        self.historical_retriever = historical_retriever
        self.candidate_merger = candidate_merger
        self.ranker = ranker
        self.finalizer = finalizer
        self.product_impact_service = product_impact_service

    async def analyze(self, command: ImpactAnalysisCommand) -> ImpactAnalysisResult:
        query_views = self.query_builder.build(command)

        semantic_candidates = await self.value_stream_retriever.retrieve(
            query_views,
            top_k=command.top_k_value_streams,
        )
        historical_support = await self.historical_retriever.retrieve(
            query_views,
            top_k=command.top_k_historical,
        )

        merged = self.candidate_merger.merge(
            semantic_candidates=semantic_candidates,
            historical_support=historical_support,
        )
        ranked = self.ranker.rank(merged)
        finalized = await self.finalizer.finalize(ranked, enabled=command.use_llm_finalizer)
        impacted_products = await self.product_impact_service.resolve(finalized)

        return ImpactAnalysisResult(
            predicted_value_streams=finalized,
            impacted_products=impacted_products,
            evidence=historical_support,
            debug={"query_views": query_views, "merged": merged},
        )
