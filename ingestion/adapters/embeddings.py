"""Adapter: embedding client dispatch (OpenAI embeddings.create or embed_many style)."""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 512


class EmbeddingClientAdapter:
    """Wraps an OpenAI or embed_many client to implement the EmbeddingClient port."""

    def __init__(
        self,
        client: Any,
        model: str | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._client = client
        self._model = model or getattr(client, "model", None) or "text-embedding-3-large"
        self._batch_size = batch_size

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        return embed_batch(
            texts,
            self._client,
            model=model or self._model,
            batch_size=self._batch_size,
        )


def embed_batch(
    texts: list[str],
    client: Any,
    model: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed texts in batches using an OpenAI-style or embed_many client."""
    model = model or getattr(client, "model", None) or "text-embedding-3-large"
    all_embeddings: list[list[float]] = []

    num_batches = math.ceil(len(texts) / batch_size) if texts else 0
    for i in range(num_batches):
        batch = texts[i * batch_size : (i + 1) * batch_size]
        batch = [t if t.strip() else " " for t in batch]
        try:
            if hasattr(client, "embeddings") and hasattr(client.embeddings, "create"):
                response = client.embeddings.create(input=batch, model=model)
                sorted_data = sorted(response.data, key=lambda d: d.index)
                all_embeddings.extend(d.embedding for d in sorted_data)
            elif hasattr(client, "embed_many"):
                all_embeddings.extend(client.embed_many(batch))
            else:
                raise TypeError(
                    "Embedding client must expose embeddings.create(...) or embed_many(...)"
                )
        except Exception as exc:
            logger.error("Embedding failed for batch %d/%d: %s", i + 1, num_batches, exc)
            dim = _get_embedding_dim(model, client=client)
            all_embeddings.extend([[0.0] * dim for _ in batch])

    return all_embeddings


def _get_embedding_dim(model: str, client: Any | None = None) -> int:
    client_dim = getattr(client, "dimension", 0) if client is not None else 0
    if isinstance(client_dim, int) and client_dim > 0:
        return client_dim
    dims = {
        "text-embedding-3-large": 3072,
        "text-embedding-3-small": 1536,
        "text-embedding-ada-002": 1536,
        "multilingual-e5-large": 1024,
    }
    for key, dim in dims.items():
        if key in model:
            return dim
    return 1536
