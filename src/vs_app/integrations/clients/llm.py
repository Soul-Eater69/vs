"""
LangChain-compatible LLM client wired to the IDP OpenAI-compatible gateway.

Usage:

    from vs_app.integrations.clients.llm import IDPChatOpenAI

    llm = IDPChatOpenAI(model="gpt-4-mini-idp")
    reply = llm.invoke([{"role": "user", "content": "Hello"}])
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

import httpx
from pydantic import Field, SecretStr, model_validator

from vs_app import settings as config
from .auth import IDPCustomAuth

try:
    import openai
except ImportError:  # pragma: no cover - optional dependency for type hints only
    openai = None

try:
    from langchain_openai import ChatOpenAI
except ImportError:  # pragma: no cover - optional dependency in some local envs
    class ChatOpenAI:  # type: ignore[no-redef]
        pass

try:
    from langchain_core.outputs import ChatGenerationChunk, ChatResult
except ImportError:  # pragma: no cover - optional dependency in some local envs
    ChatResult = Any  # type: ignore[assignment]
    ChatGenerationChunk = Any  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Reasoning-class models (o-series, gpt-5*) reject any non-default value for
# temperature, top_p, presence_penalty, or frequency_penalty. The gateway
# returns HTTP 400 (invalid_request_error / unsupported_value) when any of
# those fields is present. We detect these by the model name and strip the
# offending fields at the client layer so callers can keep passing whatever
# sampling params they want without each call site having to know which
# model family is in use.
_REASONING_MODEL_PATTERN = re.compile(
    r"^(?:o[1-9]|gpt-5)(?:[-_.].*)?$",
    re.IGNORECASE,
)
_REASONING_FORBIDDEN_PARAMS = (
    "temperature",
    "top_p",
    "presence_penalty",
    "frequency_penalty",
)


def _is_reasoning_model(model_name: str | None) -> bool:
    if not model_name:
        return False
    return bool(_REASONING_MODEL_PATTERN.match(str(model_name).strip()))


def update_completions_uri(req: httpx.Request) -> None:
    if req.url.path == "/chat/completions":
        req.url = req.url.copy_with(path=config.CHAT_COMPLETION_PATH)


async def aupdate_completions_uri(req: httpx.Request) -> None:
    if req.url.path == "/chat/completions":
        req.url = req.url.copy_with(path=config.CHAT_COMPLETION_PATH)


http_client = httpx.Client(
    transport=httpx.HTTPTransport(retries=3, verify=False),
    event_hooks={"request": [update_completions_uri]},
    auth=IDPCustomAuth(),
)

http_async_client = httpx.AsyncClient(
    transport=httpx.AsyncHTTPTransport(retries=3, verify=False),
    event_hooks={"request": [aupdate_completions_uri]},
    auth=IDPCustomAuth(),
)


class IDPChatOpenAI(ChatOpenAI):
    """
    LangChain `ChatOpenAI` subclass preconfigured for the IDP gateway.

    Pass `model` and optionally `base_url` to override the defaults from
    `src.config`.
    """

    http_client: Any | None = Field(default=http_client, exclude=True)
    http_async_client: Any | None = Field(default=http_async_client, exclude=True)
    openai_api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(IDPCustomAuth.OPENAI_COMPAT_API_KEY),
        exclude=True,
    )

    extra_body: Mapping[str, Any] | None = {"api_version": "2024-04-01-preview"}
    temperature: float | None = 0
    openai_api_base: str = config.LLM_BASE_URL

    # --------------------------------------------------------------------------
    # Reasoning-model compatibility
    # --------------------------------------------------------------------------

    @model_validator(mode="after")
    def _strip_unsupported_reasoning_params(self) -> "IDPChatOpenAI":
        model_name = getattr(self, "model_name", None) or getattr(self, "model", None)
        if _is_reasoning_model(model_name):
            for field in _REASONING_FORBIDDEN_PARAMS:
                if getattr(self, field, None) is not None:
                    object.__setattr__(self, field, None)
            existing_kwargs = dict(getattr(self, "model_kwargs", {}) or {})
            stripped_kwargs = {
                k: v for k, v in existing_kwargs.items() if k not in _REASONING_FORBIDDEN_PARAMS
            }
            if stripped_kwargs != existing_kwargs:
                object.__setattr__(self, "model_kwargs", stripped_kwargs)
        return self

    @property
    def _default_params(self) -> dict[str, Any]:
        params = dict(super()._default_params)
        model_name = getattr(self, "model_name", None) or getattr(self, "model", None)
        if _is_reasoning_model(model_name):
            for field in _REASONING_FORBIDDEN_PARAMS:
                params.pop(field, None)
        return params

    # --------------------------------------------------------------------------
    # IDP response shape adapters
    # --------------------------------------------------------------------------

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        response_dict = (
            response if isinstance(response, dict) else response.model_dump()
        )
        if response_dict.get("error"):
            raise ValueError(response_dict["error"])
        # IDP wraps the single choice under "choice" rather than "choices"
        response_dict["choices"] = [response_dict["choice"]]
        return super()._create_chat_result(response_dict, generation_info)

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        if chunk["id"]:
            chunk["choices"] = [chunk["choice"]]
            return super()._convert_chunk_to_generation_chunk(
                chunk, default_chunk_class, base_generation_info
            )
        return None


