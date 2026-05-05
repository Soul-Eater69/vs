"""Attachment download and text extraction via MarkItDown."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Dict, List, Optional

import httpx

from .mime import (
    ATTACHMENT_CONVERT_TIMEOUT_SEC,
    ATTACHMENT_FETCH_BASE_DELAY_SEC,
    ATTACHMENT_FETCH_RETRIES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

async def get_with_retry(http_client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Download with bounded retries on transient failures."""
    delay = ATTACHMENT_FETCH_BASE_DELAY_SEC
    last_exc: Exception | None = None

    for attempt in range(1, ATTACHMENT_FETCH_RETRIES + 1):
        try:
            response = await http_client.get(url, timeout=120.0)
            if response.status_code in (408, 429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError(
                    f"retryable status {response.status_code}",
                    request=response.request,
                    response=response,
                )
            return response
        except (
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
            httpx.HTTPStatusError,
        ) as exc:
            last_exc = exc
            if attempt == ATTACHMENT_FETCH_RETRIES:
                break
            logger.warning(
                "Attachment fetch retry %d/%d for %s (%s) - waiting %.1fs",
                attempt, ATTACHMENT_FETCH_RETRIES, url, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 8.0)

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# MarkItDown stream info
# ---------------------------------------------------------------------------

def build_stream_info(mime_type: str, ext: str, filename: str) -> Any:
    """Build a MarkItDown StreamInfo for format detection. Returns None if unavailable."""
    try:
        from markitdown import StreamInfo  # type: ignore
    except ImportError:
        return None
    return StreamInfo(
        mimetype=mime_type or None,
        extension=ext or None,
        filename=filename or None,
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

async def download_attachment(
    http_client: httpx.AsyncClient,
    url_or_att: Any,
    dest_path: str = "",
) -> Any:
    """Download a single attachment.

    Supports two calling conventions:
      - download_attachment(client, url_or_att=url, dest_path=...) -> saves to disk
      - download_attachment(client, url_or_att=att_dict) -> returns raw bytes

    """
    if isinstance(url_or_att, dict):
        url = url_or_att.get("content", "")
        filename = url_or_att.get("filename", "")
    else:
        url = url_or_att
        filename = ""

    if not url:
        raise ValueError("No download URL found in attachment")

    response = await get_with_retry(http_client, url)
    response.raise_for_status()
    raw_bytes = response.content

    if dest_path:
        with open(dest_path, "wb") as f:
            f.write(raw_bytes)
        return dest_path

    return raw_bytes

# ---------------------------------------------------------------------------
# Batch text extraction
# ---------------------------------------------------------------------------

async def fetch_attachment_content(
    http_client: httpx.AsyncClient,
    attachments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Download each attachment and extract text via MarkItDown.

    Returns a list of dicts with keys: filename, mime_type, text_content, error.
    """
    try:
        from markitdown import MarkItDown  # type: ignore
        md: Optional[Any] = MarkItDown()
    except ImportError:
        logger.warning("markitdown not installed; attachment text extraction disabled.")
        md = None

    results: List[Dict[str, Any]] = []
    for att in attachments:
        url = att.get("content", "")
        filename = att.get("filename", "")
        mime_type = att.get("mimeType", "")

        if not url:
            results.append({"filename": filename, "mime_type": mime_type, "text_content": "", "error": "no_content_url"})
            continue

        if md is None:
            results.append({"filename": filename, "mime_type": mime_type, "text_content": "", "error": "markitdown_not_installed"})
            continue

        try:
            raw_content = await download_attachment(http_client, att)

            ext = f".{filename.rsplit('.', 1)[-1]}" if "." in filename else ""
            stream_info = build_stream_info(mime_type=mime_type, ext=ext, filename=filename)
            raw_bytes = io.BytesIO(raw_content)
            loop = asyncio.get_running_loop()

            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda b=raw_bytes, si=stream_info: md.convert_stream(b, stream_info=si),
                    ),
                    timeout=ATTACHMENT_CONVERT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Attachment conversion timed out after %.0fs for %s - skipping",
                    ATTACHMENT_CONVERT_TIMEOUT_SEC, filename,
                )
                results.append({"filename": filename, "mime_type": mime_type, "text_content": "", "error": "conversion_timeout"})
                continue

            results.append({"filename": filename, "mime_type": mime_type, "text_content": result.text_content or "", "error": None})

        except Exception as exc:
            logger.exception("Attachment extraction failed for %s", filename)
            results.append({"filename": filename, "mime_type": mime_type, "text_content": "", "error": f"{type(exc).__name__}: {exc}"})

    return results
