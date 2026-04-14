"""
HTTP-based embedding client targeting the IDP embedding REST endpoint.

Usage:
    from src.clients.embedding import EmbeddingClient

    client = EmbeddingClient()
    vector = client.embed("risk adjustment for Medicare")
    vectors = client.embed_many(["text a", "text b"])
"""

from __future__ import annotations

import logging
import time

import httpx

from ... import config
from ..auth import IDPCustomAuth

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