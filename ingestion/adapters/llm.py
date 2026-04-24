"""Adapter: LLM client dispatch (LangChain .invoke or OpenAI .chat.completions)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMClientAdapter:
    """Wraps a LangChain or OpenAI-SDK client to implement the LLMClient port."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        max_output_tokens: int = 1200,
        temperature: float = 0.2,
        system_prompt: str | None = None,
    ) -> str:
        return complete_text(
            prompt,
            self._client,
            model=model,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
        )


def complete_text(
    prompt: str,
    llm_client: Any,
    *,
    model: str | None = None,
    max_output_tokens: int = 1200,
    temperature: float = 0.2,
    system_prompt: str | None = None,
) -> str:
    """Dispatch to a LangChain chat client or OpenAI SDK client."""
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

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

    raise TypeError(f"Unsupported LLM client type: {type(llm_client).__name__}")
