"""
LLM generation service.

Usage
-----

    svc = GenerationService()
    reply = svc.generate(
        query="What value streams relate to risk adjustment?",
        context="Realize Risk Adjustment: ...",
    )
    print(reply.content)
"""

from __future__ import annotations

import os
from typing import Any, Optional

from langchain_core.messages import BaseMessage

from .llm import IDPChatOpenAI, build_extra_body
from vs_app import settings as config

_SYSTEM_PROMPT = (
    "You are an expert on HCSC value streams. "
    "Answer the user's question using the provided context as your primary source. "
    "Cite value stream names when referring to specific processes. "
    "If the answer cannot be determined from the context, say you don't know."
)


class GenerationService:
    """
    Wraps the IDP LLM and exposes a simple :meth:`generate` interface.

    Parameters
    ----------
    model:    LLM model identifier understood by the IDP gateway.
    base_url: Override the default gateway base URL from ``src.config``.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = config.LLM_BASE_URL,
    ) -> None:
        model = model or os.environ.get("GENERATION_LLM_MODEL", "gpt-5-idp")
        kwargs = {"model": model}
        temperature = _optional_float_env("GENERATION_LLM_TEMPERATURE")
        if temperature is not None:
            kwargs["temperature"] = temperature
        reasoning_effort = os.environ.get("GENERATION_LLM_REASONING_EFFORT")
        if reasoning_effort:
            kwargs["extra_body"] = build_extra_body(reasoning_effort=reasoning_effort)
        if base_url:
            kwargs["openai_api_base"] = base_url
        self._llm = IDPChatOpenAI(**kwargs)

    def generate(
        self,
        query: str,
        context: str = "",
        system_prompt: str | None = None,
    ) -> BaseMessage:
        """
        Generate a response to *query*, optionally grounded in *context*.

        Parameters
        ----------
        query:   The user's natural-language question.
        context: Retrieved text (e.g. matched value-stream descriptions).
                 When non-empty it is prepended to the user message.

        Returns
        -------
        LangChain ``BaseMessage`` containing the model's reply.
        Access the text via ``.content``.
        """
        if context:
            user_content = f"Context:\n{context}\n\nQuestion: {query}"
        else:
            user_content = query

        messages = [
            {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        return self._llm.invoke(messages)

    def generate_structured(
        self,
        query: str,
        output_schema: type,
        *,
        context: str = "",
        system_prompt: str = "",
    ) -> Any:
        """Generate directly into a Pydantic schema using LangChain structured output."""
        if context:
            user_content = f"Context:\n{context}\n\nQuestion: {query}"
        else:
            user_content = query

        messages = [
            {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        structured_llm = self._llm.with_structured_output(
            output_schema,
            method="function_calling",
        )
        return structured_llm.invoke(messages)


def _optional_float_env(name: str) -> float | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return float(value)
