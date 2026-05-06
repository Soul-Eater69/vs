from __future__ import annotations

import logging
from typing import Any

import httpx

from vs_app import settings as config
from vs_app.integrations.clients.auth import IDPCustomAuth

logger = logging.getLogger(__name__)


class AISearchDocumentsClient:
    """IDP AI Search document upload/update gateway client."""

    def __init__(
        self,
        *,
        base_url: str = config.AISEARCH_BASE_URL,
        app_id: str = config.AISEARCH_APP_ID,
        documents_path: str = config.AISEARCH_DOCUMENTS_API_PATH,
        timeout: float = 120.0,
    ) -> None:
        if not base_url:
            raise RuntimeError("AISEARCH_BASE_URL is required for AI Search document upload")
        self._url = f"{base_url.rstrip('/')}/{documents_path.lstrip('/')}"
        self._http = httpx.Client(
            auth=IDPCustomAuth(app_id or config.LLM_APP_ID),
            verify=False,
            timeout=timeout,
        )

    def upload_documents(
        self,
        *,
        index_name: str,
        documents: list[dict],
        batch_size: int = 1000,
    ) -> dict[str, Any]:
        """POST new documents to an AI Search index."""
        return self._send(
            "POST",
            index_name=index_name,
            documents=documents,
            batch_size=batch_size,
        )

    def update_documents(
        self,
        *,
        index_name: str,
        documents: list[dict],
        batch_size: int = 1000,
    ) -> dict[str, Any]:
        """PUT existing documents in an AI Search index."""
        return self._send(
            "PUT",
            index_name=index_name,
            documents=documents,
            batch_size=batch_size,
        )

    def _send(
        self,
        method: str,
        *,
        index_name: str,
        documents: list[dict],
        batch_size: int,
    ) -> dict[str, Any]:
        payload = {
            "index_name": index_name,
            "documents": documents,
            "batch_size": batch_size,
        }
        response = self._http.request(method, self._url, json=payload)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError(f"AI Search documents API returned non-object JSON: {type(body).__name__}")
        return body


__all__ = ["AISearchDocumentsClient"]
