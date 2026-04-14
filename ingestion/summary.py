"""
Summary generation - Section 10 of the architecture spec.

Tier A/B: LLM summarizes the top 8 chunks.
Tier C:   Description text as summary.
Tier D:   Concatenated metadata.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SUMMARY_MAX_CHARS = 6000
_LLM_RETRY_ATTEMPTS = 3
_LLM_RETRY_BASE_DELAY_S = 0.8
_LLM_QUERY_PREVIEW_CHARS = 200
_LLM_CONTENT_MAX_WORDS = 3000
_LLM_SUMMARY_MAX_TOKENS = 1000
_LLM_DERIVED_MAX_TOKENS = 1500
_KEYWORD_MAX_TOKENS = 1500

_SUMMARY_INSTRUCTION = """\
Summarize this idea card. Focus on: what problem it solves,
what is proposed, which business areas are affected,
and any technical systems or products mentioned.
Be concise - 300 to 500 words."""

_KEYWORD_INSTRUCTION = """\
Extract 5-8 context keywords from the text below. Keywords should capture:
- Business capabilities or processes mentioned
- Technical systems, platforms, or tools
- Domain terms specific to the content
- Key topics or themes

Return ONLY a JSON array of lowercase keyword strings, e.g. ["keyword1", "keyword2"].
No markdown fences. No extra text. Just the JSON array.
"""

_DERIVED_INSTRUCTION = """\
You are analyzing a Jira Idea card. Given the content below, return a JSON object with exactly these keys:

- ticket_summary_llm: A 2-3 sentence plain-language summary of the whole idea.
- problem_summary_llm: 1-2 sentences on the core problem or challenge being addressed.
- solution_summary_llm: 1-2 sentences on the proposed solution or capability.
- capability_keywords_llm: List of up to 8 short capability or feature keywords (e.g. "payment processing", "real-time alerts").
- workflow_terms_llm: List of up to 6 business process or workflow terms mentioned (e.g. "approval workflow", "onboarding").
- business_entities_llm: List of up to 8 organizational or domain entities (e.g. "Finance", "Risk Management", "Digital").
- product_mentions_llm: List of up to 8 product, system, or platform names mentioned (e.g. "SAP", "Salesforce", "API Gateway").

Return only valid JSON. No markdown fences. No extra text.
"""

def _llm_create_with_retry(
    llm_client: Any,
    *,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float = 1.0,
) -> Any:
    """Call OpenAI-shaped chat completions with transient-connection retries."""
    last_exc: Exception | None = None
    user_query = ""
    if messages:
        first = messages[0] or {}
        user_query = str(first.get("content", ""))

    query_preview = user_query[:_LLM_QUERY_PREVIEW_CHARS]
    payload_meta = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "message_count": len(messages),
        "query_chars": len(user_query),
    }

    logger.warning("LLM request payload meta: %s", payload_meta)
    logger.warning("LLM query preview (first %d chars): %s", _LLM_QUERY_PREVIEW_CHARS, query_preview)

    def is_non_retryable_auth_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "401" in text
            or "unauthorized" in text
            or "authentication" in text
            or "permissiondenied" in text
            or "403" in text
            or "forbidden" in text
        )

    for attempt in range(1, _LLM_RETRY_ATTEMPTS + 1):
        try:
            if hasattr(llm_client, "chat") and hasattr(llm_client.chat, "completions"):
                return llm_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

            if hasattr(llm_client, "invoke"):
                invoke_kwargs: dict[str, Any] = {}
                if max_tokens is not None:
                    invoke_kwargs["max_completion_tokens"] = max_tokens
                if temperature is not None and hasattr(llm_client, "temperature"):
                    try:
                        llm_client.temperature = temperature
                    except Exception:
                        pass
                response = llm_client.invoke(messages, **invoke_kwargs)

                class _Message:
                    __slots__ = ("content",)
                    def __init__(self, content: str):
                        self.content = content

                class _Choice:
                    __slots__ = ("message",)
                    def __init__(self, message: _Message):
                        self.message = message

                class _Response:
                    __slots__ = ("choices",)
                    def __init__(self, content: str):
                        self.choices = [_Choice(_Message(content))]

                return _Response(str(getattr(response, "content", "") or ""))

            raise TypeError("llm_client must expose chat.completions.create(...) or invoke(...)")
        except Exception as exc:
            last_exc = exc

            if is_non_retryable_auth_error(exc):
                logger.error(
                    "LLM auth/permission error (non-retryable): %s | payload=%s | "
                    "check SSO_TOKEN or IDP credentials/env: LLM_APP_ID, LLM_BASE_URL, "
                    "IDP_AUTH_URL, IDP_CLIENT_ID, IDP_CLIENT_SECRET, IDP_USER, IDP_PASSWORD",
                    exc,
                    payload_meta,
                )
                break

            if attempt == _LLM_RETRY_ATTEMPTS:
                break

            delay = _LLM_RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
            logger.warning(
                "LLM call attempt %d/%d failed: %s - retrying in %.1fs | payload=%s",
                attempt,
                _LLM_RETRY_ATTEMPTS,
                exc,
                delay,
                payload_meta,
            )
            logger.exception("LLM call failure traceback:")
            time.sleep(delay)

    logger.error("LLM call exhausted retries | payload=%s | query_preview=%s", payload_meta, query_preview)
    raise last_exc if last_exc is not None else RuntimeError("LLM call failed")

def _truncate_to_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])

def generate_summary(
    quality_tier: str,
    chunks: list[dict],
    metadata: dict,
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
) -> str:
    """
    Generate a summary string for the document.

    Args:
        quality_tier: 'A' | 'B' | 'C' | 'D'
        chunks:       All available content chunks.
        metadata:     Metadata dict with 'metadata_text', 'summary', etc.
        llm_client:   OpenAI-compatible client. If None, falls back to non-LLM.
        model:        LLM model name override.
    """
    if quality_tier in ("A", "B") and len(chunks) >= 1 and llm_client is not None:
        return _llm_summary(chunks, llm_client, model)

    # Fallback: longest chunk or metadata
    best_chunk = _best_chunk(chunks)
    if best_chunk and len(best_chunk.get("text", "").split()) >= 30:
        return best_chunk["text"][:1000]

    return metadata.get("metadata_text", metadata.get("summary", ""))

def _llm_summary(
    chunks: list[dict],
    llm_client: Any,
    model: Optional[str],
) -> str:
    """Use an LLM to summarize the top 8 content chunks."""
    # Pick top 8 chunks by word count, excluding boilerplate
    top_chunks = sorted(
        [c for c in chunks if not c.get("is_boilerplate")],
        key=lambda c: c.get("word_count", len(c.get("text", "").split())),
        reverse=True,
    )[:8]

    if not top_chunks:
        return ""

    input_text = "\n\n---\n\n".join(c["text"] for c in top_chunks)
    # Keep payload bounded to avoid gateway/proxy resets on very large prompts
    if len(input_text) > _SUMMARY_MAX_CHARS:
        input_text = input_text[:_SUMMARY_MAX_CHARS]
    input_text = _truncate_to_words(input_text, _LLM_CONTENT_MAX_WORDS)
    prompt = f"{_SUMMARY_INSTRUCTION}\n\n{input_text}"

    model_name = model or "gpt-4o-mini"

    try:
        response = _llm_create_with_retry(
            llm_client,
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_LLM_SUMMARY_MAX_TOKENS,
            temperature=1.0,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        print(f"LLM summary generation failed: {exc}")
        logger.warning("LLM summary failed: %s - falling back", exc)
        return _best_chunk(top_chunks)["text"][:1000] if top_chunks else ""

def generate_derived_artifacts(
    quality_tier: str,
    chunks: list[dict],
    metadata: dict,
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
) -> dict:
    """
    Generate structured derived artifacts using an LLM.

    Populates the `derived` layer with:
    ticket_summary_llm, problem_summary_llm, solution_summary_llm,
    capability_keywords_llm, workflow_terms_llm, business_entities_llm,
    product_mentions_llm

    Falls back to empty strings / lists if LLM is unavailable or fails.

    Args:
        quality_tier: 'A' | 'B' | 'C' | 'D'
        chunks:       All content chunks.
        metadata:     Ticket metadata dict.
        llm_client:   OpenAI-compatible client. If None, returns empty stubs.
        model:        LLM model override.

    Returns:
        dict matching DerivedLayer TypedDict.
    """
    _empty: dict = {
        "ticket_summary_llm": "",
        "problem_summary_llm": "",
        "solution_summary_llm": "",
        "capability_keywords_llm": [],
        "workflow_terms_llm": [],
        "business_entities_llm": [],
        "product_mentions_llm": [],
    }

    if llm_client is None or quality_tier == "D":
        return _empty

    # Select top chunks by word count, skip boilerplate
    top_chunks = sorted(
        [c for c in chunks if not c.get("is_boilerplate")],
        key=lambda c: c.get("word_count", len(c.get("text", "").split())),
        reverse=True,
    )[:10]

    if not top_chunks:
        return _empty

    input_text = "\n\n---\n\n".join(c["text"] for c in top_chunks)
    # Add metadata context
    meta_context = metadata.get("metadata_text", "")
    if meta_context:
        input_text = f"Ticket Metadata:\n{meta_context}\n\n---\n\n{input_text}"
    input_text = _truncate_to_words(input_text, _LLM_CONTENT_MAX_WORDS)

    prompt = f"{_DERIVED_INSTRUCTION}\n\nContent:\n{input_text}"
    model_name = model or "gpt-4o-mini"

    try:
        import json
        response = _llm_create_with_retry(
            llm_client,
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_LLM_DERIVED_MAX_TOKENS,
            temperature=1.0,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if model added them anyway
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
        parsed = json.loads(raw)

        return {
            "ticket_summary_llm": str(parsed.get("ticket_summary_llm", "")),
            "problem_summary_llm": str(parsed.get("problem_summary_llm", "")),
            "solution_summary_llm": str(parsed.get("solution_summary_llm", "")),
            "capability_keywords_llm": list(parsed.get("capability_keywords_llm") or []),
            "workflow_terms_llm": list(parsed.get("workflow_terms_llm") or []),
            "business_entities_llm": list(parsed.get("business_entities_llm") or []),
            "product_mentions_llm": list(parsed.get("product_mentions_llm") or []),
        }
    except Exception as exc:
        logger.warning("generate_derived_artifacts failed: %s - returning stubs", exc)
        return _empty

def _best_chunk(chunks: list[dict]) -> dict:
    """Return the chunk with the most words."""
    content_chunks = [c for c in chunks if not c.get("is_boilerplate")]
    if not content_chunks:
        return {}
    return max(content_chunks, key=lambda c: c.get("word_count", len(c.get("text", "").split())))

def _extract_json_from_llm(raw: str) -> Any:
    """
    Robustly extract JSON from LLM output.

    Handles: bare JSON, markdown-fenced JSON, thinking-prefixed output,
    and responses where the JSON is embedded in prose.
    """
    import json as _json
    import re as _re

    if not raw:
        return None

    # 1. Try direct parse first
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences
    if "```" in raw:
        parts = raw.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # odd-indexed parts are inside fences
                part = part.strip()
                if part.startswith("json"):
                    text = part[4:].strip()
                    try:
                        return _json.loads(text)
                    except _json.JSONDecodeError:
                        continue

    # 3. Find first JSON array or object in the text
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = _re.search(pattern, raw, _re.DOTALL)
        if match:
            try:
                return _json.loads(match.group(0))
            except _json.JSONDecodeError:
                continue

    return None

def extract_chunk_keywords(
    chunks: list[dict],
    llm_client: Optional[Any] = None,
    model: Optional[str] = None,
    max_keywords: int = 8,
) -> None:
    """
    Extract context keywords for each chunk using an LLM.

    Mutates each chunk dict in-place, adding a "context_keywords" list.
    Batches multiple short chunks into a single LLM call to reduce cost.
    Falls back to empty list when LLM is unavailable.
    """
    import json as _json

    if llm_client is None:
        for c in chunks:
            c["context_keywords"] = []
        return

    model_name = model or "gpt-4o-mini"

    # Strategy: batch chunks into groups to minimize LLM calls
    # Each batch is < 2000 words of content. We ask the LLM to return
    # one keyword array per chunk in the batch.
    _BATCH_MAX_WORDS = 800
    batches: list[list[int]] = []  # List of lists of chunk indices
    current_batch: list[int] = []
    current_words = 0

    for i, chunk in enumerate(chunks):
        wc = chunk.get("word_count") or len(str(chunk.get("text", "")).split())
        if current_words + wc > _BATCH_MAX_WORDS and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_words = 0
        current_batch.append(i)
        current_words += wc

    if current_batch:
        batches.append(current_batch)

    for batch_indices in batches:
        batch_chunks = [chunks[i] for i in batch_indices]
        if len(batch_chunks) == 1:
            # Single chunk - simple prompt
            text = _truncate_to_words(str(batch_chunks[0].get("text", "")), 600)
            prompt = f"{_KEYWORD_INSTRUCTION}\n\nText:\n{text}"

            try:
                resp = _llm_create_with_retry(
                    llm_client,
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=_KEYWORD_MAX_TOKENS,
                    temperature=1.0,
                )
                raw = resp.choices[0].message.content.strip()
                keywords = _extract_json_from_llm(raw)
                if isinstance(keywords, list):
                    chunks[batch_indices[0]]["context_keywords"] = [
                        str(k).lower().strip() for k in keywords
                    ][:max_keywords]
                else:
                    chunks[batch_indices[0]]["context_keywords"] = []
            except Exception as exc:
                logger.warning("Keyword extraction failed for chunk %d: %s", batch_indices[0], exc)
                chunks[batch_indices[0]]["context_keywords"] = []
        else:
            # Multi-chunk batch
            numbered = []
            for j, bc in enumerate(batch_chunks):
                text = _truncate_to_words(str(bc.get("text", "")), 400)
                numbered.append(f"Chunk {j}:\n{text}")

            combined = "\n\n".join(numbered)
            batch_prompt = (
                f"Extract 5-8 context keywords for EACH of the {len(batch_chunks)} "
                f"chunks below. Return a JSON array of arrays, one inner array per chunk.\n"
                f"No markdown fences. Just the JSON.\n\n{combined}"
            )

            try:
                resp = _llm_create_with_retry(
                    llm_client,
                    model=model_name,
                    messages=[{"role": "user", "content": batch_prompt}],
                    max_tokens=_KEYWORD_MAX_TOKENS,
                    temperature=1.0,
                )
                raw = resp.choices[0].message.content.strip()
                parsed = _extract_json_from_llm(raw)
                if isinstance(parsed, list):
                    for idx_in_batch, original_idx in enumerate(batch_indices):
                        if idx_in_batch < len(parsed) and isinstance(parsed[idx_in_batch], list):
                            chunks[original_idx]["context_keywords"] = [
                                str(k).lower().strip() for k in parsed[idx_in_batch]
                            ][:max_keywords]
                        else:
                            chunks[original_idx]["context_keywords"] = []
                else:
                    for idx in batch_indices:
                        chunks[idx]["context_keywords"] = []
            except Exception as exc:
                logger.warning("Batch keyword extraction failed: %s", exc)
                for idx in batch_indices:
                    chunks[idx]["context_keywords"] = []

    logger.info(
        "Extracted keywords for %d chunks (%d batches)",
        len(chunks),
        len(batches),
    )

# Re-export Any for the type hint above
from typing import Any # type: ignore (placed here to avoid circular import confusion)