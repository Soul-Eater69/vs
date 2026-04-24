"""Port: text embedding contract."""

from __future__ import annotations

from typing import Protocol


class EmbeddingClient(Protocol):
    """Structural protocol for any text embedding client."""

    def embed(
        self,
        texts: list[str],
        model: str | None = None,
    ) -> list[list[float]]: ...
