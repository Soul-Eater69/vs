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
from typing import Any, Mapping

import httpx
from pydantic import Field, SecretStr

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


def complete_text(
    prompt: str,
    llm_client: Any,
    *,
    model: str | None = None,
    max_output_tokens: int = 1200,
    temperature: float = 0.2,
    system_prompt: str | None = None,
) -> str:
    """
    Return plain text from either the project's LangChain chat client or an
    OpenAI-compatible SDK client.

    This keeps ingestion code agnostic to which concrete chat client was wired.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        if hasattr(llm_client, "invoke"):
            reply = llm_client.invoke(messages)
            content = getattr(reply, "content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text")
                        if text:
                            parts.append(str(text))
                return "\n".join(parts).strip()
            return str(content or "").strip()

        if hasattr(llm_client, "chat") and hasattr(llm_client.chat, "completions"):
            response = llm_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_output_tokens,
                temperature=temperature,
            )
            choice = response.choices[0].message
            return str(getattr(choice, "content", "") or "")
    except Exception:
        raise

    raise TypeError(f"Unsupported LLM client type: {type(llm_client).__name__}")
