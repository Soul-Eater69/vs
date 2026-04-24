from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, Iterable, List, Optional

from vs_app.integrations.clients.generation_service import GenerationService
from vs_app.modules.prompts.loader import load_rag_prompts, render_prompt, safe_json_extract
from vs_app.modules.rag.query.views import clean_ppt_text, normalize_for_search
from vs_app.shared.constants import CANONICAL_VALUE_STREAMS
from .retrieval_pipeline import (
    build_historical_vs_evidence,
    load_ticket_vs_maps,
    retrieve_historical_chunks,
    retrieve_kg_candidates,
    sanitize_selected,
)

logger = logging.getLogger(__name__)


def _norm_name(value: Optional[str]) -> str:
    return normalize_for_search((value or "").strip())


def _short(text: str, max_chars: int = 220) -> str:
    text = clean_ppt_text(text or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _dedupe_by_name(rows: Iterable[dict]) -> List[dict]:
    out: List[dict] = []
    seen: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _norm_name(row.get("entity_name") or row.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(row)

    return out


def _chunk_score(chunk: dict) -> float:
    return float(chunk.get("_score") or chunk.get("best_score") or chunk.get("score") or 0.0)


def _chunk_ticket_id(chunk: dict) -> str:
    return str(chunk.get("sourceId") or chunk.get("ticket_id") or chunk.get("source_id") or "").strip()


def _chunk_text(chunk: dict) -> str:
    return str(chunk.get("content") or chunk.get("text") or chunk.get("snippet") or "").strip()


def _chunk_provenance_text(chunk: dict) -> str:
    prov = chunk.get("chunkProvenance") or {}
    if isinstance(prov, str):
        try:
            prov = json.loads(prov)
        except Exception:
            prov = {}

    if not isinstance(prov, dict):
        prov = {}

    page_range = prov.get("pageRange") or []
    slide_range = prov.get("slideRange") or []
    source_type = str(prov.get("sourceType") or "").strip()

    if isinstance(page_range, list) and page_range:
        if len(page_range) == 1:
            where = f"page-{page_range[0]}"
        else:
            where = f"pages-{page_range[0]}-{page_range[-1]}"
    elif isinstance(slide_range, list) and slide_range:
        if len(slide_range) == 1:
            where = f"slide-{slide_range[0]}"
        else:
            where = f"slides-{slide_range[0]}-{slide_range[-1]}"
    else:
        where = ""

    return ":".join([p for p in [source_type, where] if p])


def _limit_historical_chunks(chunks: List[dict], limit: int) -> List[dict]:
    if not chunks:
        return []

    ranked = sorted(
        chunks,
        key=lambda chunk: (_chunk_score(chunk), len(_chunk_text(chunk).split())),
        reverse=True,
    )

    per_ticket_cap = 2
    per_ticket: Dict[str, int] = {}
    selected: List[dict] = []
    overflow: List[dict] = []

    for chunk in ranked:
        ticket_id = _chunk_ticket_id(chunk)
        if ticket_id:
            count = per_ticket.get(ticket_id, 0)
            if count >= per_ticket_cap:
                overflow.append(chunk)
                continue
            per_ticket[ticket_id] = count + 1

        selected.append(chunk)
        if len(selected) >= limit:
            return selected

    for chunk in overflow:
        selected.append(chunk)
        if len(selected) >= limit:
            break

    return selected


def _compact_historical_evidence(chunks: List[dict], limit: int = 6) -> List[dict]:
    compact: List[dict] = []

    for chunk in _limit_historical_chunks(chunks, limit):
        compact.append(
            {
                "ticket_id": _chunk_ticket_id(chunk),
                "title": _short(str(chunk.get("title") or ""), 120),
                "score": round(_chunk_score(chunk), 4),
                "snippet": _short(_chunk_text(chunk), 280),
                "provenance": _short(_chunk_provenance_text(chunk), 100),
            }
        )

    return compact


def _compact_vs_support(vs_support: List[dict], limit: int = 8) -> List[dict]:
    rows = sorted(
        vs_support,
        key=lambda row: (
            int(row.get("support_count") or 0),
            float(row.get("best_support_score") or row.get("confidence") or row.get("score") or 0.0),
        ),
        reverse=True,
    )

    compact: List[dict] = []
    for row in _dedupe_by_name(rows)[:limit]:
        compact.append(
            {
                "entity_id": row.get("entity_id") or "",
                "entity_name": row.get("entity_name") or "",
                "support_count": int(row.get("support_count") or 0),
                "best_support_score": round(
                    float(row.get("best_support_score") or row.get("confidence") or row.get("score") or 0.0),
                    4,
                ),
                "supporting_ticket_ids": [str(t) for t in (row.get("supporting_ticket_ids") or [])][:5],
                "source_theme_id": row.get("source_theme_id") or "",
            }
        )

    return compact


def _compact_candidates(candidates: List[dict], limit: int = 12) -> List[dict]:
    rows = sorted(
        candidates,
        key=lambda row: (
            float(row.get("best_score") or row.get("score") or 0.0),
            int(row.get("support_count") or 0),
        ),
        reverse=True,
    )

    compact: List[dict] = []
    for row in _dedupe_by_name(rows)[:limit]:
        compact.append(
            {
                "entity_id": row.get("entity_id") or row.get("id") or "",
                "entity_name": row.get("entity_name") or row.get("name") or "",
                "category": row.get("category") or "",
                "score": round(float(row.get("best_score") or row.get("score") or 0.0), 4),
                "description": _short(str(row.get("description") or row.get("value_proposition") or ""), 220),
            }
        )

    return compact


def _inject_missing_historical_candidates(candidates: List[dict], vs_support: List[dict]) -> int:
    existing = {
        _norm_name(c.get("entity_name"))
        for c in candidates
        if isinstance(c, dict) and c.get("entity_name")
    }

    # canonical ids already present in KG candidates by name
    candidate_by_name = {
        _norm_name(c.get("entity_name")): c
        for c in candidates
        if isinstance(c, dict) and c.get("entity_name")
    }

    injected = 0
    for row in vs_support:
        if not isinstance(row, dict):
            continue

        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue

        key = _norm_name(name)
        if key in existing:
            continue

        kg_match = candidate_by_name.get(key)

        candidates.append(
            {
                "entity_id": (kg_match or {}).get("entity_id") or row.get("entity_id") or "",
                "entity_name": (kg_match or {}).get("entity_name") or name,
                "description": f"Recovered from historical ticket support ({int(row.get('support_count') or 0)} similar tickets).",
                "value_proposition": "",
                "support_count": int(row.get("support_count") or 0),
                "score": float(row.get("best_support_score") or row.get("score") or 0.0),
                "candidate_position": "historical_trigger",
                "source": "historical_support",
            }
        )
        existing.add(key)
        injected += 1

    with open("debug_injected_candidates.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "injected_count": injected,
                "candidates": candidates,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return injected


def _fallback_from_support(vs_support: List[dict], candidates: List[dict], top_n: int = 8) -> List[dict]:
    by_name = {
        _norm_name(candidate.get("entity_name")): candidate
        for candidate in candidates
        if candidate.get("entity_name")
    }

    selected: List[dict] = []
    for row in _compact_vs_support(vs_support, limit=top_n * 2):
        name = str(row.get("entity_name") or "").strip()
        key = _norm_name(name)
        if not name or key not in by_name:
            continue

        candidate = by_name[key]
        support_count = int(row.get("support_count") or 0)
        best_support_score = float(row.get("best_support_score") or 0.0)

        selected.append(
            {
                "entity_id": candidate.get("entity_id") or row.get("entity_id") or "",
                "entity_name": candidate.get("entity_name") or name,
                "confidence": round(min(0.95, 0.40 + 0.06 * support_count + 0.15 * min(best_support_score, 1.0)), 4),
                "reason": "Fallback from historical ticket support.",
                "description": candidate.get("description") or "",
                "supporting_ticket_ids": row.get("supporting_ticket_ids", []),
                "supporting_chunk_ids": row.get("supporting_chunk_ids", []),
            }
        )
        if len(selected) >= top_n:
            break

    return selected


def _fallback_from_candidates(candidates: List[dict], top_n: int = 8) -> List[dict]:
    return [
        {
            "entity_id": row.get("entity_id") or row.get("id") or "",
            "entity_name": row.get("entity_name") or row.get("name") or "",
            "confidence": 0.45,
            "reason": "Fallback from top KG candidates (LLM output empty/unavailable).",
            "description": row.get("description") or "",
            "supporting_ticket_ids": [],
            "supporting_chunk_ids": [],
        }
        for row in _compact_candidates(candidates, limit=top_n)
    ]


def _filter_to_allowed(rows: List[dict], allowed_names: Optional[List[str]]) -> List[dict]:
    if not allowed_names:
        return rows

    allowed = {_norm_name(name) for name in allowed_names if name}
    return [row for row in rows if _norm_name(row.get("entity_name")) in allowed]


def _inject_explicit_canonical_subset(
    rows: List[dict],
    canonical_always_include: Optional[List[str]],
    allowed_names: Optional[List[str]],
) -> int:
    if not canonical_always_include:
        return 0

    allowed = {_norm_name(name) for name in allowed_names or [] if name}
    canonical_index = {_norm_name(v.get("name")): v for v in CANONICAL_VALUE_STREAMS}
    existing = {_norm_name(row.get("entity_name")) for row in rows if row.get("entity_name")}
    injected = 0

    for name in canonical_always_include:
        key = _norm_name(name)
        if not key or key in existing:
            continue

        if allowed and key not in allowed:
            continue

        canon = canonical_index.get(key)
        if not canon:
            continue

        rows.append(
            {
                "entity_id": canon.get("id") or "",
                "entity_name": canon.get("name") or name,
                "confidence": 1.0,
                "reason": "Explicit canonical include.",
                "category": canon.get("category") or "",
                "supporting_ticket_ids": [],
                "supporting_chunk_ids": [],
            }
        )
        existing.add(key)
        injected += 1

    return injected


def _build_prompt_payload(
    cleaned_text: str,
    historical_chunks: List[dict],
    vs_support: List[dict],
    candidates: List[dict],
    max_historical_chunks: int,
    max_candidate_streams: int,
) -> tuple[list[dict], list[dict], list[dict], str]:
    compact_evidence = _compact_historical_evidence(historical_chunks, limit=min(6, max_historical_chunks))
    compact_support = _compact_vs_support(vs_support, limit=8)
    compact_candidates = _compact_candidates(candidates, limit=min(12, max_candidate_streams))
    ppt_text_for_prompt = normalize_for_search(cleaned_text)[:1500]
    return compact_evidence, compact_support, compact_candidates, ppt_text_for_prompt


def generate_value_streams_rag(
    ppt_text: str,
    allowed_value_stream_names: Optional[List[str]] = None,
    local_vs_map_dir: str = "ticket_chunks",
    top_per_view: int = 12,
    max_historical_chunks: int = 60,
    max_candidate_streams: int = 50,
    llm_max_retries: int = 2,
    canonical_always_include: Optional[List[str]] = None,
) -> dict:
    """RAG pipeline for value-stream selection."""

    import os
    os.makedirs("debug_json", exist_ok=True)

    cleaned_text = clean_ppt_text(ppt_text)

    if not cleaned_text.strip():
        return {
            "selected_value_streams": [],
            "rejected_candidates": [],
            "historical_ticket_hits": [],
            "historical_value_stream_support": [],
            "candidate_value_streams": [],
            "raw_response": None,
            "warnings": ["empty_input_after_cleaning"],
            "error": "empty_input_text",
        }

    historical_chunks = retrieve_historical_chunks(cleaned_text, top_k_per_view=top_per_view)
    with open("debug_json/debug_historical_chunks.json", "w", encoding="utf-8") as f:
        json.dump(historical_chunks, f, ensure_ascii=False, indent=2, default=str)
        
    historical_chunks = _limit_historical_chunks(historical_chunks, max_historical_chunks)
    with open("debug_json/debug_limited_historical_chunks.json", "w", encoding="utf-8") as f:
        json.dump(historical_chunks, f, ensure_ascii=False, indent=2, default=str)
        
    vs_map = load_ticket_vs_maps(local_vs_map_dir)
    with open("debug_json/debug_ticket_vs_maps.json", "w", encoding="utf-8") as f:
        json.dump(vs_map, f, ensure_ascii=False, indent=2)

    ranked_tickets, vs_support = build_historical_vs_evidence(
        historical_chunks,
        vs_map,
        max_ticket_hits=12,
    )
    vs_support = _filter_to_allowed(vs_support, allowed_value_stream_names)
    
    candidates = retrieve_kg_candidates(
        cleaned_text,
        top_k=max_candidate_streams,
        allowed_names=allowed_value_stream_names,
    )
    with open("debug_json/debug_initial_candidates.json", "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2, default=str)
        
    injected = _inject_missing_historical_candidates(candidates, vs_support)
    with open("debug_json/debug_injected_candidates.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "injected_count": injected,
                "candidates": candidates,
            },
            f,
            ensure_ascii=False,
            indent=2,
            default=str
        )

    candidates = _filter_to_allowed(candidates, allowed_value_stream_names)
    t4 = time.time()
    
    prompts = load_rag_prompts()
    system_prompt = prompts.get("system", "")
    scope = (
        f"Use only this explicit subset: {allowed_value_stream_names}."
        if allowed_value_stream_names
        else "Use only the provided candidate value streams."
    )

    compact_evidence, compact_support, compact_candidates, ppt_text_for_prompt = _build_prompt_payload(
        cleaned_text=cleaned_text,
        historical_chunks=historical_chunks,
        vs_support=vs_support,
        candidates=candidates,
        max_historical_chunks=max_historical_chunks,
        max_candidate_streams=max_candidate_streams,
    )

    user_prompt = render_prompt(
        prompts["selection_user"],
        ppt_text=ppt_text_for_prompt,
        historical_evidence_json=json.dumps(compact_evidence, ensure_ascii=False),
        historical_vs_support_json=json.dumps(compact_support, ensure_ascii=False),
        candidate_value_streams_json=json.dumps(compact_candidates, ensure_ascii=False),
        selection_scope=scope,
    )

    with open("debug_json/debug_rag_prompt.txt", "w", encoding="utf-8") as f:
        f.write(f"=== User Prompt ===\n{user_prompt}\n\n")

    parsed: Optional[dict] = None
    raw_response: Optional[str] = None
    warnings: List[str] = []
    llm_ok = False

    for attempt in range(1, llm_max_retries + 1):
        try:
            reply = GenerationService().generate(
                query=user_prompt,
                context="",
                system_prompt=system_prompt,
            )

            raw_response = reply.content
            parsed = safe_json_extract(raw_response)

            if isinstance(parsed, list):
                parsed = {
                    "selected_value_streams": parsed,
                    "rejected_candidates": [],
                }

            elif not isinstance(parsed, dict):
                parsed = {
                    "selected_value_streams": [],
                    "rejected_candidates": [],
                }

            parsed = sanitize_selected(parsed, candidates)

            parsed["selected_value_streams"] = _filter_to_allowed(
                parsed.get("selected_value_streams", []),
                allowed_value_stream_names,
            )

            llm_ok = True
            break

        except Exception as exc:
            print(f"[RAG-LLM] Attempt {attempt} failed: {exc}")
            if attempt < llm_max_retries:
                time.sleep(3 * attempt)
            else:
                warnings.append(f"llm_unavailable_fallback_used:{type(exc).__name__}")

    if not llm_ok:
        fallback = _fallback_from_support(vs_support, candidates, top_n=8)
        if not fallback:
            fallback = _fallback_from_candidates(candidates, top_n=8)
        parsed = {"selected_value_streams": fallback, "rejected_candidates": []}

    elif not parsed or not parsed.get("selected_value_streams"):
        fallback = _fallback_from_support(vs_support, candidates, top_n=8)
        if not fallback:
            fallback = _fallback_from_candidates(candidates, top_n=8)
        parsed = {
            "selected_value_streams": fallback,
            "rejected_candidates": parsed.get("rejected_candidates", []) if parsed else [],
        }

    canon_injected = _inject_explicit_canonical_subset(
        parsed["selected_value_streams"],
        canonical_always_include=canonical_always_include,
        allowed_names=allowed_value_stream_names,
    )
    if canon_injected:
        logger.info("[RAG] Injected %d explicit canonical value streams", canon_injected)

    final = _dedupe_by_name(parsed.get("selected_value_streams", []))
    final = _filter_to_allowed(final, allowed_value_stream_names)

    return {
        "selected_value_streams": final,
        "rejected_candidates": parsed.get("rejected_candidates", []),
        "historical_ticket_hits": ranked_tickets,
        "historical_ticket_evidence": compact_evidence,
        "historical_value_stream_support": compact_support,
        "candidate_value_streams": compact_candidates,
        "raw_response": raw_response,
        "warnings": warnings,
    }
