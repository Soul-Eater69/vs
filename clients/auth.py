from __future__ import annotations

import logging
import os
import threading
import time
import urllib3

import httpx
import requests

import config

# Suppress InsecureRequestWarning from requests (verify=False)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token fetch - uses `requests` (not httpx) to match the proven
# impact_analysis pattern that the IDP endpoint actually accepts.
# ---------------------------------------------------------------------------


def _fetch_idp_token(
    url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> str:
    """Fetch a JWT from the IDP token endpoint using `requests`.

    This mirrors the known-working ``get_idp_access_token`` from
    impact_analysis exactly: credentials go in **headers**, user/pass
    go in the **JSON body**, and the response carries ``jwt_token``.

    Raises ``RuntimeError`` if the token cannot be obtained.
    """
    url = url or config.IDP_AUTH_URL
    client_id = client_id or config.IDP_CLIENT_ID
    client_secret = client_secret or config.IDP_CLIENT_SECRET
    user = user or config.IDP_USER
    password = password or config.IDP_PASSWORD

    headers = {
        "Accept": "*/*",
        "ClientSecret": client_secret,
        "Content-Type": "application/json",
        "ClientId": client_id,
        "scope": "profile openid roles permissions",
    }

    data = {
        "password": password,
        "username": user,
    }

    response = requests.post(url, headers=headers, json=data, verify=False)
    response.raise_for_status()
    body = response.json()

    token = body.get("jwt_token") or body.get("access_token") or body.get("token")
    if not token:
        raise RuntimeError(
            f"IDP response did not contain a token. Keys: {list(body.keys())}"
        )

    logger.info("IDP token acquired from %s", url)
    return str(token)


# ---------------------------------------------------------------------------
# HTTPX Auth adapter - injects bearer token into every LLM request
# ---------------------------------------------------------------------------


class IDPCustomAuth(httpx.Auth):
    """HTTPX auth adapter for IDP-backed bearer token authentication.

    Token acquisition uses :func:`_fetch_idp_token` (backed by ``requests``)
    which matches the proven IDP contract. The adapter caches the token and
    auto-refreshes on 401.
    """

    requires_request_body = True
    OPENAI_COMPAT_API_KEY = "idp-auth-managed"

    # Default TTL for cached tokens (seconds). The IDP typically returns
    # "expires_in" but we fall back to this if absent.
    _DEFAULT_TTL = 300

    def __init__(self, app_id: str | None = None) -> None:
        self._app_id = app_id or config.LLM_APP_ID
        self._token: str | None = None
        self._expires_at_epoch: float = 0.0
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------------
    # Token management
    # ---------------------------------------------------------------------------

    def _token_valid(self) -> bool:
        return bool(self._token) and time.time() < (self._expires_at_epoch - 30)

    def _fetch_token(self) -> tuple[str | None, float]:
        """Acquire a fresh token, trying the configured URL first and falling
        back to a corrected variant when "compact=true_all" is detected."""
        auth_url = config.IDP_AUTH_URL
        urls_to_try: list[str] = [auth_url]

        if "true_all" in (auth_url or ""):
            corrected = auth_url.replace("compact=true_all", "compact=true")
            if corrected != auth_url:
                urls_to_try.append(corrected)
                logger.info(
                    "IDP_AUTH_URL contains 'compact=true_all'; will also "
                    "try corrected URL: %s",
                    corrected,
                )

        last_exc: Exception | None = None
        for url in urls_to_try:
            try:
                token = _fetch_idp_token(url=url)
                return token, time.time() + self._DEFAULT_TTL
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        print(
            "Unable to fetch IDP auth token from any URL. Last error: %s. "
            "Request will proceed WITHOUT bearer token - expect 401.",
            last_exc,
        )
        return None, 0.0

    def _ensure_token(self) -> str | None:
        """Return a valid token, fetching / refreshing as needed."""
        sso_token = os.environ.get("SSO_TOKEN")
        if sso_token:
            sso_token = sso_token.strip()
            if sso_token.lower() in {"none", "null", "undefined"} or sso_token.count(".") != 2:
                logger.warning("Ignoring invalid SSO_TOKEN; falling back to IDP token fetch")
            else:
                return sso_token

        if self._token_valid():
            return self._token

        with self._lock:
            if self._token_valid():
                return self._token
            token, expires_at = self._fetch_token()
            self._token = token
            self._expires_at_epoch = expires_at
            return self._token

    def _refresh_token(self) -> str | None:
        """Force-refresh the cached IDP token (ignored when SSO_TOKEN is set)."""
        sso = os.environ.get("SSO_TOKEN")
        if sso:
            return sso
        with self._lock:
            token, expires_at = self._fetch_token()
            self._token = token
            self._expires_at_epoch = expires_at
            return self._token

    # ---------------------------------------------------------------------------
    # httpx Auth flow
    # ---------------------------------------------------------------------------

    def auth_flow(self, request: httpx.Request):
        token = self._ensure_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        request.headers["Content-Type"] = "application/json"
        request.headers["app-id"] = str(self._app_id)

        response = yield request

        # Auto-refresh on 401 and retry once.
        if response.status_code == 401 and not os.environ.get("SSO_TOKEN"):
            logger.info("IDP request returned 401; refreshing token and retrying once")
            refreshed = self._refresh_token()
            if refreshed:
                retry_request = request.copy()
                retry_request.headers["Authorization"] = f"Bearer {refreshed}"
                retry_request.headers["Content-Type"] = "application/json"
                retry_request.headers["app-id"] = str(self._app_id)
                yield retry_request

    async def async_auth_flow(self, request: httpx.Request):
        token = self._ensure_token()
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        request.headers["Content-Type"] = "application/json"
        request.headers["app-id"] = str(self._app_id)

        response = yield request

        if response.status_code == 401 and not os.environ.get("SSO_TOKEN"):
            logger.info("IDP async request returned 401; refreshing token and retrying once")
            refreshed = self._refresh_token()
            if refreshed:
                retry_request = request.copy()
                retry_request.headers["Authorization"] = f"Bearer {refreshed}"
                retry_request.headers["Content-Type"] = "application/json"
                retry_request.headers["app-id"] = str(self._app_id)
                yield retry_request
