"""Plain value-stream selection pipeline.

Flow: PPT text -> semantic vector search -> LLM selection.
"""

from __future__ import annotations

import logging
import re
from typing import List

from src.clients.azure_direct_client import AzureDirectSearchClient
from src.services.value_stream_generation_service import GenerationService
from core.text import clean_ppt_text, extract_signal_lines, extract_signal_sections, normalize_for_search
from core.prompts import safe_json_extract, build_plain_selection_prompt
from .retrieval_pipeline import (
    build_context,
)

logger = logging.getLogger(__name__)

_SEMANTIC_NOISE_PATTERNS = [
    r"\bidea card executive summary\b",
    r"\bid\b(:|\s)*",
]


def _collapse_adjacent_phrase_repetition(text: str, min_window: int = 3, max_window: int = 8) -> str:
    """Collapse adjacent repeated token windows (common in OCR/header duplication)."""
    tokens = (text or "").split()
    if len(tokens) < min_window * 2:
        return text

    output: List[str] = []
    index = 0
    total_tokens = len(tokens)

    while index < total_tokens:
        collapsed = False
        for window in range(max_window, min_window - 1, -1):
            if index + (2 * window) > total_tokens:
                continue
            
            phrase = tokens[index : index + window]
            next_phrase = tokens[index + window : index + (2 * window)]
            if phrase == next_phrase:
                jump = index + window
                while jump + window <= total_tokens and tokens[jump : jump + window] == phrase:
                    jump += window
                
                output.extend(phrase)
                index = jump
                collapsed = True
                break
        
        if not collapsed:
            output.append(tokens[index])
            index += 1

    return " ".join(output)


def _clean_semantic_query_text(text: str) -> str:
    """Remove boilerplate and repetition so retrieval query keeps only useful business signal."""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = _collapse_adjacent_phrase_repetition(cleaned)

    for pattern in _SEMANTIC_NOISE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\b(executive|sponsor|accountable|responsible|functional\s+portfolio|owner)\b.*", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _render_structured_signal_query(parsed: dict) -> str:
    """Build a deterministic retrieval query from structured BA signal fields."""
    if not isinstance(parsed, dict):
        return ""

    def _as_list(value) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        value_str = str(value).strip()
        return [value_str] if value_str else []

    parts: List[str] = []

    problem = str(parsed.get("problem") or "").strip()
    if problem:
        parts.append(f"problem: {problem}")

    context = str(parsed.get("context") or "").strip()
    if context:
        parts.append(f"context: {context}")

    stakeholders = _as_list(parsed.get("stakeholders"))
    if stakeholders:
        parts.append(f"stakeholders: " + ", ".join(stakeholders[:8]))

    outcomes = _as_list(parsed.get("desired_outcomes"))
    if outcomes:
        parts.append(f"desired outcomes: " + ", ".join(outcomes[:8]))

    constraints = _as_list(parsed.get("constraints"))
    if constraints:
        parts.append(f"constraints: " + ", ".join(constraints[:6]))

    keywords = _as_list(parsed.get("domain_keywords"))
    if keywords:
        parts.append(f"keywords: " + ", ".join(keywords[:14]))

    return " | ".join(parts)


def _build_semantic_search_query(raw_query: str, gen_svc: GenerationService) -> str:
    """Create a compact signal summary for semantic retrieval, with safe fallback."""
    cleaned_query = clean_ppt_text(raw_query)
    sections = extract_signal_sections(cleaned_query)
    signal_lines = extract_signal_lines(cleaned_query, max_lines=80, max_chars=9000)

    section_payload = "\n\n".join(
        f"[{name}]: {str(text)[:900]}"
        for name, text in sections.items()
        if str(text).strip()
    )

    source_payload = (section_payload or signal_lines or cleaned_query)[:10000]

    summary_prompt = (
        "========================================================================\n"
        f"TASK: Extract BA-grade signals for semantic value stream discovery\n"
        f"You are a business analyst. Convert noisy PPT text into structured signal fields\n"
        f"used to retrieve matching value streams (end-to-end workflows that solve the problem).\n\n"
        f"GOAL: Produce focused, non-repetitive signals that maximize semantic retrieval quality.\n\n"
        "========================================================================\n"
        f"OUTPUT FORMAT: Return VALID JSON only with exactly these keys:\n"
        f"problem\ncontext\nstakeholders\ndesired_outcomes\nconstraints\ndomain_keywords\n\n"
        f"Field rules:\n"
        f"- problem/context: short strings\n"
        f"- stakeholders/desired_outcomes/constraints/domain_keywords: arrays of short strings\n"
        f"- domain_keywords: 8-14 retrieval-friendly terms\n\n"
        "========================================================================\n"
        f"RULES:\n"
        f"1. Think like a BA: capture problem-to-outcome chain, not slide metadata\n"
        f"2. Exclude governance/header noise (idea card labels, IDs, sponsor/owner lines)\n"
        f"3. Do not repeat phrases or headings\n"
        f"4. Keep strings concise and specific; no paragraphs\n"
        f"5. Return JSON only\n\n"
        "========================================================================\n"
        f"CONTENT:\n\n{source_payload}\n\n"
    )

    try:
        reply = gen_svc.generate(
            query=summary_prompt,
            context="",
            system_prompt="You are an expert at concise retrieval query rewriting for semantic search.",
        )
        parsed = safe_json_extract(getattr(reply, "content", "") or "")
        summary = _render_structured_signal_query(parsed)
        if summary:
            return normalize_for_search(_clean_semantic_query_text(summary), max_chars=1400)
    except Exception as exc:
        logger.warning("Semantic summary generation failed, using fallback query: %s", exc)

    fallback = section_payload or signal_lines or cleaned_query
    return normalize_for_search(_clean_semantic_query_text(fallback), max_chars=1400)


def select_value_streams(query: str, fetch_count: int = 12) -> dict:
    """
    Run the plain pipeline:
    1. Semantic hybrid retrieval against value-stream index
    2. Limit candidates
    3. LLM selection
    """
    client = AzureDirectSearchClient()
    gen_svc = GenerationService()

    top_k = max(35, fetch_count)
    
    try:
        results = client.search_hybrid(
            query,
            top_k=top_k,
            use_semantic_rerank=True,
            filter_expression="node_type eq 'ValueStream'",
        )
    except Exception as exc:
        logger.warning("Hybrid semantic reranking unavailable, falling back to vector search: %s", exc)
        results = client.search_vector(query, top_k=top_k, filter_expression="node_type eq 'ValueStream'")

    if not results:
        return {
            "selected_value_streams": [],
            "rejected_candidates": [],
            "raw_response": None,
            "candidates_used": [],
        }

    candidate_pool: List[dict] = list(results)[:top_k]
    context = build_context(candidate_pool)
    prompt = build_plain_selection_prompt(query=query, context=context)
    print(prompt)
    reply = gen_svc.generate(query=prompt, context="")
    parsed = safe_json_extract(reply.content)

    return {
        "selected_value_streams": parsed.get("selected_value_streams", []),
        "rejected_candidates": parsed.get("rejected_candidates", []),
        "raw_response": reply.content,
        "candidates_used": [
            {
                "entity_id": m.get("entity_id", ""),
                "entity_name": m.get("entity_name", ""),
                "score": (
                    m.get("@search.reranker_score")
                    if m.get("@search.reranker_score") is not None
                    else m.get("@search.score", 0)
                ) or 0,
                "@search.score": m.get("@search.score", 0),
                "@search.reranker_score": m.get("@search.reranker_score", 0),
                "_support_count": m.get("_support_count", 1),
                "_aggregated_best_score": m.get("_aggregated_best_score", 0),
            }
            for m in candidate_pool
        ],
    }