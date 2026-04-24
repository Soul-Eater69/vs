"""Probe link accessibility: HEAD then GET-Range fallback, SharePoint detection."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from .models import LinkAccessStatus, LinkCandidate

logger = logging.getLogger(__name__)

_TIMEOUT = 12.0
_RANGE_HEADER = "bytes=0-8191"

_SHAREPOINT_INDICATORS = (
    "sharepoint.com",
    "sharepoint-df.com",
    "my.sharepoint",
    "/_layouts/",
    "/sites/",
    "/_api/",
    "microsoftonline.com",
    "login.microsoftonline",
)

_AUTH_REDIRECT_KEYWORDS = (
    "login",
    "signin",
    "/auth",
    "adfs",
    "sso",
    "microsoftonline",
    "federatedlogin",
)


def _is_sharepoint_url(url: str) -> bool:
    lower = url.lower()
    return any(ind in lower for ind in _SHAREPOINT_INDICATORS)


def _location_is_auth(headers: httpx.Headers) -> bool:
    location = headers.get("location", "").lower()
    return any(kw in location for kw in _AUTH_REDIRECT_KEYWORDS)


def _location_is_sharepoint(headers: httpx.Headers) -> bool:
    location = headers.get("location", "").lower()
    return any(ind in location for ind in _SHAREPOINT_INDICATORS)


async def probe_link(candidate: LinkCandidate) -> LinkCandidate:
    """Probe a single link and return updated candidate with access_status populated."""
    url = candidate.url
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return candidate.model_copy(
            update={
                "access_status": LinkAccessStatus.unsupported_scheme,
                "not_accessible_reason": f"Unsupported scheme: {parsed.scheme!r}",
            }
        )

    if _is_sharepoint_url(url):
        return await _probe_sharepoint(candidate)

    return await _probe_generic(candidate)


async def _probe_sharepoint(candidate: LinkCandidate) -> LinkCandidate:
    """Best-effort probe for SharePoint URLs; expect auth failure."""
    url = candidate.url
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=_TIMEOUT,
            follow_redirects=False,
        ) as client:
            try:
                resp = await client.head(url)
            except Exception:
                resp = None

            if resp is not None:
                if resp.status_code in (301, 302, 303, 307, 308):
                    if _location_is_sharepoint(resp.headers) or _location_is_auth(resp.headers):
                        return candidate.model_copy(
                            update={
                                "access_status": LinkAccessStatus.sharepoint_auth_required,
                                "http_status": resp.status_code,
                                "final_url": resp.headers.get("location"),
                                "not_accessible_reason": "SharePoint/auth redirect detected",
                            }
                        )
                if resp.status_code in (401, 403):
                    return candidate.model_copy(
                        update={
                            "access_status": LinkAccessStatus.sharepoint_auth_required,
                            "http_status": resp.status_code,
                            "not_accessible_reason": f"SharePoint returned {resp.status_code}",
                        }
                    )
    except Exception:
        pass

    return candidate.model_copy(
        update={
            "access_status": LinkAccessStatus.sharepoint_auth_required,
            "not_accessible_reason": "SharePoint URL — auth assumed required",
        }
    )


async def _probe_generic(candidate: LinkCandidate) -> LinkCandidate:
    url = candidate.url
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await _head_then_get(client, url)

        final_url = str(resp.url)
        http_status = resp.status_code
        content_type = resp.headers.get("content-type", "")

        if 200 <= http_status < 300:
            if _is_sharepoint_url(final_url):
                return candidate.model_copy(
                    update={
                        "access_status": LinkAccessStatus.sharepoint_auth_required,
                        "http_status": http_status,
                        "final_url": final_url,
                        "content_type": content_type,
                        "not_accessible_reason": "Redirected to SharePoint auth page",
                    }
                )
            if _looks_like_auth_page(resp):
                return candidate.model_copy(
                    update={
                        "access_status": LinkAccessStatus.auth_required,
                        "http_status": http_status,
                        "final_url": final_url,
                        "content_type": content_type,
                        "not_accessible_reason": "Redirected to login/auth page",
                    }
                )
            return candidate.model_copy(
                update={
                    "access_status": LinkAccessStatus.accessible,
                    "http_status": http_status,
                    "final_url": final_url,
                    "content_type": content_type,
                    "not_accessible_reason": "",
                }
            )

        status_map = {
            401: (LinkAccessStatus.auth_required, "HTTP 401 auth required"),
            403: (LinkAccessStatus.forbidden, "HTTP 403 forbidden"),
            404: (LinkAccessStatus.not_found, "HTTP 404 not found"),
        }
        if http_status in status_map:
            status, reason = status_map[http_status]
            if http_status == 403 and _is_sharepoint_url(final_url):
                status = LinkAccessStatus.sharepoint_auth_required
                reason = "SharePoint 403"
            return candidate.model_copy(
                update={
                    "access_status": status,
                    "http_status": http_status,
                    "final_url": final_url,
                    "not_accessible_reason": reason,
                }
            )

        return candidate.model_copy(
            update={
                "access_status": LinkAccessStatus.unknown_error,
                "http_status": http_status,
                "final_url": final_url,
                "not_accessible_reason": f"Unexpected HTTP status {http_status}",
            }
        )

    except httpx.TimeoutException:
        return candidate.model_copy(
            update={
                "access_status": LinkAccessStatus.timeout,
                "not_accessible_reason": "Request timed out",
            }
        )
    except httpx.ConnectError as exc:
        return candidate.model_copy(
            update={
                "access_status": LinkAccessStatus.connection_error,
                "not_accessible_reason": f"Connection error: {exc}",
            }
        )
    except Exception as exc:
        logger.warning("Link probe failed for %s: %s", url, exc)
        return candidate.model_copy(
            update={
                "access_status": LinkAccessStatus.unknown_error,
                "not_accessible_reason": f"{type(exc).__name__}: {exc}",
            }
        )


async def _head_then_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """Try HEAD; fall back to GET with Range on failure or 405."""
    try:
        resp = await client.head(url)
        if resp.status_code != 405:
            return resp
    except Exception:
        pass
    return await client.get(url, headers={"Range": _RANGE_HEADER})


def _looks_like_auth_page(resp: httpx.Response) -> bool:
    final = str(resp.url).lower()
    return any(kw in final for kw in _AUTH_REDIRECT_KEYWORDS)
