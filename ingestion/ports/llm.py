"""Port: LLM text completion contract."""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """Structural protocol for any LLM completion client."""

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_output_tokens: int = 1200,
        temperature: float = 0.2,
        system_prompt: str | None = None,
    ) -> str: ...
