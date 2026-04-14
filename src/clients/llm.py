"""
LangChain-compatible LLM client wired to the IDP OpenAI-compatible gateway.

Usage:

    from src.clients.llm import IDPChatOpenAI

    llm = IDPChatOpenAI(model="gpt-4-mini-idp")
    reply = llm.invoke([{"role": "user", "content": "Hello"}])
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping

import httpx
import openai
from langchain_openai import ChatOpenAI
from langchain_core.outputs import ChatResult, ChatGenerationChunk
from pydantic import Field, SecretStr

from .. import config
from ..auth import IDPCustomAuth

logger = logging.getLogger(__name__)


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
    temperature: float | None = 1
    openai_api_base: str = config.LLM_BASE_URL

    # --------------------------------------------------------------------------
    # IDP response shape adapters
    # --------------------------------------------------------------------------

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
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