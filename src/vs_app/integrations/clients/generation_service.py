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
        model = model or os.environ.get("GENERATION_LLM_MODEL", "gpt-5-mini-idp")
        kwargs = {"model": model}
        reasoning_effort = os.environ.get("GENERATION_LLM_REASONING_EFFORT", "medium")
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
        reasoning_effort: str | None = None,
    ) -> Any:
        """Generate directly into a Pydantic schema using LangChain structured output.

        Uses include_raw=True so a malformed/partial tool call from the gateway does
        not raise — instead we get {"raw": AIMessage, "parsed": Model | None,
        "parsing_error": Exception | None}. If parsing fails, we salvage the tool-call
        args manually and coerce them into the schema, and only fall back to an empty
        instance if every salvage path fails. This is critical for review-pool selection
        where an exception would error the whole pipeline; with this wrapper the worst
        case is a low pick count that the deterministic backfill then tops up.
        """
        import json
        import logging as _logging

        _local_logger = _logging.getLogger(__name__)

        if context:
            user_content = f"Context:\n{context}\n\nQuestion: {query}"
        else:
            user_content = query

        messages = [
            {"role": "system", "content": system_prompt or _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        llm = self._llm
        if reasoning_effort:
            llm = llm.bind(extra_body=build_extra_body(reasoning_effort=reasoning_effort))
        structured_llm = llm.with_structured_output(
            output_schema,
            method="function_calling",
            include_raw=True,
        )
        envelope = structured_llm.invoke(messages)

        # When include_raw=True langchain returns a dict, not the model directly.
        if isinstance(envelope, dict):
            parsed = envelope.get("parsed")
            if parsed is not None:
                return parsed

            raw = envelope.get("raw")
            parsing_error = envelope.get("parsing_error")
            _local_logger.warning(
                "generate_structured: parsing_error=%s; attempting manual salvage from tool_calls",
                parsing_error,
            )

            tool_calls = getattr(raw, "tool_calls", None) or []
            for call in tool_calls:
                args = call.get("args") if isinstance(call, dict) else None
                if isinstance(args, dict):
                    try:
                        return output_schema(**args)
                    except Exception:
                        continue

            content = getattr(raw, "content", "") or ""
            if isinstance(content, str) and content.strip():
                start, end = content.find("{"), content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        return output_schema(**json.loads(content[start : end + 1]))
                    except Exception:
                        pass

            _local_logger.warning(
                "generate_structured: manual salvage failed; returning empty %s",
                output_schema.__name__,
            )
            return output_schema()

        return envelope
