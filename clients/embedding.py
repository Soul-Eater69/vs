"""
HTTP-based embedding client targeting the IDP embedding REST endpoint.

Usage:
    from clients.embedding import EmbeddingClient, embed_batch

    client = EmbeddingClient()
    vector = client.embed("risk adjustment for Medicare")
    vectors = client.embed_many(["text a", "text b"])
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Optional

import httpx

import config
from clients.auth import IDPCustomAuth

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """
    Wraps the IDP embedding endpoint.

    The instance is thread-safe and intended to be created once per process.
    It also satisfies the LangChain 'embeddings' interface so it can be
    passed directly to 'Chroma' or other LangChain vector stores.
    """

    def __init__(
        self,
        app_id: str = config.LLM_APP_ID,
        model: str = config.EMBEDDING_MODEL,
        dimension: int = config.EMBEDDING_DIMENSION,
        base_url: str = config.LLM_BASE_URL,
        max_retries: int = 4,
        base_retry_delay_sec: float = 1.0,
    ):
        self.model = model
        self.dimension = dimension
        self.max_retries = max_retries
        self.base_retry_delay_sec = base_retry_delay_sec
        self._url = f"{base_url}/api/v1/embeddings"
        try:
            self._http = httpx.Client(
                auth=IDPCustomAuth(app_id), verify=False, timeout=60.0
            )
        except Exception as exc:
            logger.warning("Failed to initialize EmbeddingClient HTTP client: %s", exc)
            raise

    # --------------------------------------------------------------------------
    # Primary interface
    # --------------------------------------------------------------------------

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text (batched in a single call)."""
        payload = {
            "api_version": "2024-04-01",
            "input": texts,
            "model": self.model,
            "encoding_format": "float",
            "dimensions": self.dimension,
        }
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._http.post(self._url, json=payload)
                response.raise_for_status()
                return [entry["vector"] for entry in response.json()["embeddings"]]
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                is_retryable = status is not None and status >= 500
                if not is_retryable or attempt >= self.max_retries:
                    raise
                delay = self.base_retry_delay_sec * (2 ** (attempt - 1))
                logger.warning(
                    "Embedding request retry %d/%d after HTTP %s; waiting %.1fs",
                    attempt,
                    self.max_retries,
                    status,
                    delay,
                )
                time.sleep(delay)
            except (httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                if attempt >= self.max_retries:
                    raise
                delay = self.base_retry_delay_sec * (2 ** (attempt - 1))
                logger.warning(
                    "Embedding request retry %d/%d after %s; waiting %.1fs",
                    attempt,
                    self.max_retries,
                    type(exc).__name__,
                    delay,
                )
                time.sleep(delay)
        raise RuntimeError("Embedding request failed after retries")

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for 'text'."""
        return self.embed_many([text])[0]

    # --------------------------------------------------------------------------
    # LangChain Embeddings interface aliases
    # --------------------------------------------------------------------------

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_many(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed(text)

    def __call__(self, text: str) -> list[float]:
        """Compatibility shim for vector stores that still call the embedding object directly."""
        return self.embed(text)


DEFAULT_BATCH_SIZE = 512  # OpenAI allows up to 2048 inputs per call


def embed_batch(
    texts: list[str],
    client: Any,
    model: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[float]]:
    """Embed a list of texts in batches using either our client or an OpenAI-style client."""
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
                    "embedding client must expose embeddings.create(...) or embed_many(...)"
                )
        except Exception as exc:
            logger.error("Embedding failed for batch %d/%d: %s", i + 1, num_batches, exc)
            dim = _get_embedding_dim(model, client=client)
            all_embeddings.extend([[0.0] * dim for _ in batch])

    return all_embeddings


def _get_embedding_dim(model: str, client: Any | None = None) -> int:
    """Return the expected dimensionality for common embedding models."""
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
