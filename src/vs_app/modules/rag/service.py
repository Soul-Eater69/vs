"""Historical RAG service facade for value-stream prediction."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Literal


@dataclass(slots=True)
class ValueStreamRagCommand:
    query: str | None = None
    ticket_id: str | None = None
    idea_card_text: str | None = None
    source: Literal["jira"] | None = None
    fetch_count: int = 12
    historical_faiss_dir: str = "ticket_data/_faiss"
    historical_search_backend: str | None = None
    historical_azure_index_name: str | None = None
    allowed_value_stream_names: list[str] | None = None
    use_llm_finalizer: bool = True
    top_k_historical: int | None = None
    top_k_value_streams: int | None = None
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
    rescued_confirmed_merged_value_streams: list[dict]
    rescued_historical_gap_fill_value_streams: list[dict]
    dropped_historical_gap_fill_value_streams: list[dict]
    rejected_candidates: list[dict]
    semantic_candidate_value_streams: list[dict]
    historical_candidate_value_streams: list[dict]
    merged_candidate_value_streams: list[dict]
    historical_ticket_hits: list[dict]
    historical_value_stream_support: list[dict]
    candidate_value_streams: list[dict]
    llm_candidates: list[dict]
    historical_source: str
    raw_response: Any
    direct_llm_output: Any
    historical_llm_output: Any
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
        query_builder: Any = None,
        value_stream_retriever: Any = None,
        historical_retriever: Any = None,
        candidate_merger: Any = None,
        ranker: Any = None,
        finalizer: Any = None,
        *_legacy_components: Any,
        pipeline_fn: Callable[..., dict] | None = None,
        **_legacy_kwargs: Any,
    ) -> None:
        self.query_builder = query_builder
        self.value_stream_retriever = value_stream_retriever
        self.historical_retriever = historical_retriever
        self.candidate_merger = candidate_merger
        self.ranker = ranker
        self.finalizer = finalizer
        self.pipeline_fn = pipeline_fn

    async def analyze(self, command: ValueStreamRagCommand) -> ValueStreamRagResult:
        if self._has_composed_flow():
            payload = await self._run_composed_flow(command)
        else:
            payload = await self._run_pipeline_flow(command)
        return self._result_from_payload(payload)

    async def predict(self, command: ValueStreamRagCommand) -> ValueStreamRagResult:
        return await self.analyze(command)

    def _has_composed_flow(self) -> bool:
        required = (
            self.query_builder,
            self.value_stream_retriever,
            self.historical_retriever,
            self.candidate_merger,
        )
        return all(component is not None for component in required)

    async def _run_pipeline_flow(self, command: ValueStreamRagCommand) -> dict:
        from .pipeline import select_value_streams

        query = self._resolve_query(command)
        exclude_ids = _source_ticket_exclusions(command)
        return (self.pipeline_fn or select_value_streams)(
            query,
            fetch_count=self._resolve_fetch_count(command),
            historical_faiss_dir=command.historical_faiss_dir,
            historical_search_backend=command.historical_search_backend,
            historical_azure_index_name=command.historical_azure_index_name,
            allowed_value_stream_names=command.allowed_value_stream_names,
            exclude_ticket_ids=exclude_ids,
            semantic_fetch_k=command.semantic_fetch_k,
            historical_ticket_fetch_k=command.historical_ticket_fetch_k,
            llm_candidate_window=command.llm_candidate_window,
            final_output_count=command.final_output_count,
        )

    async def _run_composed_flow(self, command: ValueStreamRagCommand) -> dict:
        query_views = self.query_builder.build(command)

        semantic_candidates = await self._maybe_await(
            self.value_stream_retriever.retrieve(
                query_views,
                top_k=self._resolve_top_k_value_streams(command),
            )
        )
        historical_support = await self._maybe_await(
            self.historical_retriever.retrieve(
                query_views,
                top_k=self._resolve_top_k_historical(command),
            )
        )

        merged = self.candidate_merger.merge(
            semantic_candidates=semantic_candidates,
            historical_support=historical_support,
        )
        ranked = self.ranker.rank(merged) if self.ranker is not None else merged

        if self.finalizer is not None:
            finalized = await self._maybe_await(
                self.finalizer.finalize(
                    ranked,
                    enabled=command.use_llm_finalizer,
                )
            )
        else:
            finalized = ranked

        if isinstance(finalized, dict):
            payload = dict(finalized)
        else:
            payload = {"selected_value_streams": list(finalized or [])}

        payload.setdefault("auto_selected_value_streams", [])
        payload.setdefault("llm_selected_value_streams", [])
        payload.setdefault("rescued_confirmed_merged_value_streams", [])
        payload.setdefault("rescued_historical_gap_fill_value_streams", [])
        payload.setdefault("dropped_historical_gap_fill_value_streams", [])
        payload.setdefault("rejected_candidates", [])
        payload.setdefault("semantic_candidate_value_streams", list(semantic_candidates or []))
        payload.setdefault("historical_candidate_value_streams", list(historical_support or []))
        payload.setdefault("merged_candidate_value_streams", list(merged or []))
        payload.setdefault("historical_ticket_hits", [])
        payload.setdefault("historical_value_stream_support", list(historical_support or []))
        payload.setdefault("candidate_value_streams", list(merged or []))
        payload.setdefault("llm_candidates", [])
        payload.setdefault("historical_source", "composed")
        payload.setdefault("raw_response", None)
        payload.setdefault("direct_llm_output", None)
        payload.setdefault("historical_llm_output", None)
        payload.setdefault("query_preparation", {})
        payload.setdefault("warnings", [])
        payload.setdefault("evidence", list(historical_support or []))
        payload.setdefault("debug", {"query_views": query_views, "merged": merged})
        payload.setdefault("historical_excluded_ticket_ids", _source_ticket_exclusions(command) or [])
        return payload

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _resolve_query(command: ValueStreamRagCommand) -> str:
        query = command.query or command.idea_card_text
        if query:
            return str(query)
        if command.ticket_id:
            return str(command.ticket_id)
        raise ValueError("ValueStreamRagCommand requires query or idea_card_text")

    @staticmethod
    def _resolve_fetch_count(command: ValueStreamRagCommand) -> int:
        if command.fetch_count:
            return int(command.fetch_count)
        candidates = [value for value in (command.top_k_historical, command.top_k_value_streams) if value]
        return max(int(value) for value in candidates) if candidates else 12

    @staticmethod
    def _resolve_top_k_historical(command: ValueStreamRagCommand) -> int:
        return int(command.top_k_historical or command.fetch_count or 12)

    @staticmethod
    def _resolve_top_k_value_streams(command: ValueStreamRagCommand) -> int:
        return int(command.top_k_value_streams or command.fetch_count or 12)

    @staticmethod
    def _result_from_payload(payload: dict[str, Any]) -> ValueStreamRagResult:
        return ValueStreamRagResult(
            selected_value_streams=list(payload.get("selected_value_streams", []) or []),
            auto_selected_value_streams=list(payload.get("auto_selected_value_streams", []) or []),
            llm_selected_value_streams=list(payload.get("llm_selected_value_streams", []) or []),
            rescued_confirmed_merged_value_streams=list(
                payload.get("rescued_confirmed_merged_value_streams", []) or []
            ),
            rescued_historical_gap_fill_value_streams=list(
                payload.get("rescued_historical_gap_fill_value_streams", []) or []
            ),
            dropped_historical_gap_fill_value_streams=list(
                payload.get("dropped_historical_gap_fill_value_streams", []) or []
            ),
            rejected_candidates=list(payload.get("rejected_candidates", []) or []),
            semantic_candidate_value_streams=list(payload.get("semantic_candidate_value_streams", []) or []),
            historical_candidate_value_streams=list(payload.get("historical_candidate_value_streams", []) or []),
            merged_candidate_value_streams=list(payload.get("merged_candidate_value_streams", []) or []),
            historical_ticket_hits=list(payload.get("historical_ticket_hits", []) or []),
            historical_value_stream_support=list(payload.get("historical_value_stream_support", []) or []),
            candidate_value_streams=list(payload.get("candidate_value_streams", []) or []),
            llm_candidates=list(payload.get("llm_candidates", []) or []),
            historical_source=str(payload.get("historical_source", "") or ""),
            raw_response=payload.get("raw_response"),
            direct_llm_output=payload.get("direct_llm_output"),
            historical_llm_output=payload.get("historical_llm_output"),
            query_preparation=dict(payload.get("query_preparation", {}) or {}),
            warnings=list(payload.get("warnings", []) or []),
            evidence=list(payload.get("evidence", payload.get("historical_value_stream_support", [])) or []),
            debug=dict(payload.get("debug", {}) or {}),
            historical_excluded_ticket_ids=list(payload.get("historical_excluded_ticket_ids", []) or []),
        )


RagImpactService = ValueStreamRagService
ImpactAnalysisCommand = ValueStreamRagCommand
ImpactAnalysisResult = ValueStreamRagResult


def _source_ticket_exclusions(command: ValueStreamRagCommand) -> list[str] | None:
    if not command.exclude_source_ticket_from_historical or not command.ticket_id:
        return None
    return [command.ticket_id]

__all__ = [
    "ValueStreamRagService",
    "ValueStreamRagCommand",
    "ValueStreamRagResult",
    "RagImpactService",
    "ImpactAnalysisCommand",
    "ImpactAnalysisResult",
]
