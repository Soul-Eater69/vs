"""Historical RAG service facade for value-stream prediction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class ValueStreamRagCommand:
    query: str | None = None
    ticket_id: str | None = None
    idea_card_text: str | None = None
    historical_faiss_dir: str = "ticket_data/_faiss"
    historical_search_backend: str | None = None
    historical_azure_index_name: str | None = None
    foundational_value_streams_raw: list[str] | None = None
    foundational_value_streams_canonical: list[str] | None = None
    foundational_value_stream_entity_ids: list[str] | None = None
    foundational_value_stream_matches: list[dict] | None = None
    semantic_fetch_k: int = 40
    historical_ticket_fetch_k: int = 35
    llm_candidate_window: int = 30
    final_output_count: int | None = None
    exclude_source_ticket_from_historical: bool = True


@dataclass(slots=True)
class ValueStreamRagResult:
    selected_value_streams: list[dict]
    auto_selected_value_streams: list[dict]
    llm_selected_value_streams: list[dict]
    rejected_candidates: list[dict]
    semantic_candidate_value_streams: list[dict]
    historical_candidate_value_streams: list[dict]
    merged_candidate_value_streams: list[dict]
    historical_ticket_hits: list[dict]
    historical_value_stream_support: list[dict]
    candidate_value_streams: list[dict]
    llm_candidates: list[dict]
    foundational_signals: list[str]
    foundational_signal_source: str
    foundational_value_stream_matches: list[dict]
    historical_source: str
    raw_response: Any
    review_pool_llm_output: Any
    rag_runtime_config: dict[str, Any]
    query_preparation: dict[str, Any]
    warnings: list[Any]
    evidence: list[dict]
    debug: dict[str, Any]
    historical_excluded_ticket_ids: list[str]

    @property
    def predicted_value_streams(self) -> list[dict]:
        return self.selected_value_streams


class ValueStreamRagService:
    def __init__(
        self,
        pipeline_fn: Callable[..., dict] | None = None,
    ) -> None:
        self.pipeline_fn = pipeline_fn

    async def analyze(self, command: ValueStreamRagCommand) -> ValueStreamRagResult:
        payload = await self._run_pipeline_flow(command)
        return self._result_from_payload(payload)

    async def predict(self, command: ValueStreamRagCommand) -> ValueStreamRagResult:
        return await self.analyze(command)

    async def _run_pipeline_flow(self, command: ValueStreamRagCommand) -> dict:
        from .pipeline import select_value_streams

        query = self._resolve_query(command)
        exclude_ids = _source_ticket_exclusions(command)
        return (self.pipeline_fn or select_value_streams)(
            query,
            historical_faiss_dir=command.historical_faiss_dir,
            historical_search_backend=command.historical_search_backend,
            historical_azure_index_name=command.historical_azure_index_name,
            exclude_ticket_ids=exclude_ids,
            semantic_fetch_k=command.semantic_fetch_k,
            historical_ticket_fetch_k=command.historical_ticket_fetch_k,
            llm_candidate_window=command.llm_candidate_window,
            final_output_count=command.final_output_count,
            foundational_value_streams_raw=command.foundational_value_streams_raw,
            foundational_value_streams_canonical=command.foundational_value_streams_canonical,
            foundational_value_stream_entity_ids=command.foundational_value_stream_entity_ids,
            foundational_value_stream_matches=command.foundational_value_stream_matches,
        )

    @staticmethod
    def _resolve_query(command: ValueStreamRagCommand) -> str:
        query = command.query or command.idea_card_text
        if query:
            return str(query)
        if command.ticket_id:
            return str(command.ticket_id)
        raise ValueError("ValueStreamRagCommand requires query or idea_card_text")

    @staticmethod
    def _result_from_payload(payload: dict[str, Any]) -> ValueStreamRagResult:
        return ValueStreamRagResult(
            selected_value_streams=list(payload.get("selected_value_streams", []) or []),
            auto_selected_value_streams=list(payload.get("auto_selected_value_streams", []) or []),
            llm_selected_value_streams=list(payload.get("llm_selected_value_streams", []) or []),
            rejected_candidates=list(payload.get("rejected_candidates", []) or []),
            semantic_candidate_value_streams=list(payload.get("semantic_candidate_value_streams", []) or []),
            historical_candidate_value_streams=list(payload.get("historical_candidate_value_streams", []) or []),
            merged_candidate_value_streams=list(payload.get("merged_candidate_value_streams", []) or []),
            historical_ticket_hits=list(payload.get("historical_ticket_hits", []) or []),
            historical_value_stream_support=list(payload.get("historical_value_stream_support", []) or []),
            candidate_value_streams=list(payload.get("candidate_value_streams", []) or []),
            llm_candidates=list(payload.get("llm_candidates", []) or []),
            foundational_signals=list(payload.get("foundational_signals", []) or []),
            foundational_signal_source=str(payload.get("foundational_signal_source", "") or ""),
            foundational_value_stream_matches=list(
                payload.get("foundational_value_stream_matches", []) or []
            ),
            historical_source=str(payload.get("historical_source", "") or ""),
            raw_response=payload.get("raw_response"),
            review_pool_llm_output=payload.get("review_pool_llm_output"),
            rag_runtime_config=dict(payload.get("rag_runtime_config", {}) or {}),
            query_preparation=dict(payload.get("query_preparation", {}) or {}),
            warnings=list(payload.get("warnings", []) or []),
            evidence=list(payload.get("evidence", payload.get("historical_value_stream_support", [])) or []),
            debug=dict(payload.get("debug", {}) or {}),
            historical_excluded_ticket_ids=list(payload.get("historical_excluded_ticket_ids", []) or []),
        )


def _source_ticket_exclusions(command: ValueStreamRagCommand) -> list[str] | None:
    if not command.exclude_source_ticket_from_historical or not command.ticket_id:
        return None
    return [command.ticket_id]

__all__ = [
    "ValueStreamRagService",
    "ValueStreamRagCommand",
    "ValueStreamRagResult",
]
