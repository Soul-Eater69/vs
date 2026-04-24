"""
retrieval.py

Retrieval helpers shared by the plain and RAG pipelines.

Includes:
- retrieval view construction
- LLM content analysis
- search wrappers
- candidate aggregation / context-building
- historical chunk retrieval
- local ticket->value-stream map loading
- historical ticket->value-stream support building
- KG candidate retrieval
- LLM output sanitization
"""

from __future__ import annotations

import glob
import json
import logging
import pathlib
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from vs_app.integrations.clients.azure_direct_client import AzureDirectSearchClient
from vs_app.integrations.clients.generation_service import GenerationService
from vs_app.modules.prompts.loader import safe_json_extract
from vs_app.modules.rag.query.views import (
    clean_ppt_text,
    extract_signal_sections,
    normalize_for_search,
)
from vs_app.shared.constants import CANONICAL_VALUE_STREAMS, PRECHUNK_DIR

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Simple search wrapper
# ----------------------------------------------------------------------

def run_vector_search(query: str, top_k: int = 15) -> List[dict]:
    """Run a plain vector search against the default KG index and return results."""
    client = AzureDirectSearchClient()
    results = client.search_vector(query, top_k=top_k)
    if not results:
        return []

    return [
        {
            "entity_id": doc.get("entity_id", ""),
            "entity_name": doc.get("entity_name", ""),
            "@search.score": doc.get("@search.score", 0),
            "@search.reranker_score": doc.get("@search.reranker_score", 0),
        }
        for doc in results
    ]


# ----------------------------------------------------------------------
# Content analysis (LLM)
# ----------------------------------------------------------------------

def analyze_idea_card_content(raw_text: str) -> dict:
    """Use LLM to extract healthcare domain concepts for retrieval."""
    gen_svc = GenerationService()
    cleaned = clean_ppt_text(raw_text)

    prompt = f"""Analyze this healthcare idea card and extract key information. Return valid JSON ONLY.

        Pay special attention to:
        - Any mention of compliance, regulatory, requirements, standards, audit, risk management
        - Healthcare domain or specialty (behavioral health, oncology, cardiology, women's health, etc.)
        - Clinical vs operational focus
        - Patient population or member segment

        IDEA CARD TEXT:
        {cleaned[:2000]}

        Return JSON in this exact format:
        {{
            "domain": "string",
            "primary_pain_points": ["string"],
            "objectives": ["string"],
            "key_themes": ["string"],
            "has_compliance_focus": true,
            "regulatory_themes": ["string"],
            "keywords": "space-separated keywords"
        }}"""

    try:
        response = gen_svc.generate(query=prompt, context="")
        return safe_json_extract(response.content)
    except Exception as exc:
        logger.warning("Failed to analyze idea card content: %s", exc)
        return {
            "domain": "general",
            "primary_pain_points": [],
            "objectives": [],
            "key_themes": [],
            "has_compliance_focus": False,
            "regulatory_themes": [],
            "keywords": "healthcare",
        }


# ----------------------------------------------------------------------
# Retrieval views
# ----------------------------------------------------------------------

def build_retrieval_views(raw_text: str) -> List[str]:
    """Build multiple retrieval views from the idea-card PPT content."""
    cleaned = clean_ppt_text(raw_text)
    sections = extract_signal_sections(raw_text)
    analysis = analyze_idea_card_content(raw_text)

    executive = sections.get("Idea Card Executive Summary:", "")
    problem = sections.get("Problem Statement/Market Opportunity:", "")
    solution = sections.get("Business Solution and Objectives:", "")
    alternative = sections.get("Alternative Solutions:", "")
    value_prop = sections.get("Value Proposition & Key Metrics:", "")
    interdep = sections.get("Interdependencies:", "")
    costs = sections.get("Estimated Costs:", "")

    view1 = normalize_for_search(cleaned, max_chars=2200)
    view2 = normalize_for_search(" ".join([executive, value_prop]), max_chars=2200)
    view3 = normalize_for_search(" ".join([problem, solution, alternative[:800]]), max_chars=2500)
    view4 = normalize_for_search(" ".join([solution, alternative, interdep]), max_chars=2500)
    view5 = normalize_for_search(" ".join([executive, value_prop, interdep, costs]), max_chars=2500)

    domain_kw = " ".join(
        [
            f"domain: {analysis.get('domain', '')}",
            "pain points: " + " ".join(analysis.get("primary_pain_points", [])),
            "objectives: " + " ".join(analysis.get("objectives", [])),
            "themes: " + " ".join(analysis.get("key_themes", [])),
            analysis.get("keywords", ""),
        ]
    ).strip()

    common_kw = (
        "utilization management member services provider care coordination "
        "compliance regulatory requirements risk audit quality "
        "operational efficiency process improvement workflow "
        "revenue cycle financial management billing claims "
        "product development market pricing commercial"
    )

    if analysis.get("has_compliance_focus") or analysis.get("regulatory_themes"):
        domain_kw += " compliance regulatory requirements ensure compliance " + " ".join(
            analysis.get("regulatory_themes", [])
        )

    domain_kw += " " + common_kw
    view6 = normalize_for_search(domain_kw, max_chars=1200)

    deduped: List[str] = []
    seen: set[str] = set()

    for view in [view1, view2, view3, view4, view5, view6]:
        key = view.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(view)

    return deduped


# ----------------------------------------------------------------------
# Candidate helpers
# ----------------------------------------------------------------------

def is_canonical_value_stream(entity_name: str) -> bool:
    name_lower = entity_name.lower().strip()
    return any(vs.get("name", "").lower().strip() == name_lower for vs in CANONICAL_VALUE_STREAMS)


def _effective_retrieval_score(match: dict) -> float:
    reranker = match.get("@search.reranker_score")
    if reranker is not None:
        return float(reranker or 0.0)
    return float(match.get("@search.score", 0.0) or 0.0)


def aggregate_matches(all_matches: List[dict]) -> List[dict]:
    """Merge candidates across retrieval views, ranking by support count then score."""
    by_id: Dict[str, dict] = {}

    for match in all_matches:
        entity_id = match.get("entity_id")
        if not entity_id:
            continue

        score = _effective_retrieval_score(match)

        if entity_id not in by_id:
            by_id[entity_id] = {
                "doc": match,
                "best_score": score,
                "support_count": 1,
            }
        else:
            entry = by_id[entity_id]
            entry["best_score"] = max(entry["best_score"], score)
            entry["support_count"] += 1

            current = _effective_retrieval_score(entry["doc"])
            if score > current:
                entry["doc"] = match

    ranked = sorted(by_id.values(), key=lambda row: (row["support_count"], row["best_score"]), reverse=True)

    docs: List[dict] = []
    for item in ranked:
        doc = dict(item["doc"])
        doc["_support_count"] = item["support_count"]
        doc["_aggregated_best_score"] = item["best_score"]
        docs.append(doc)

    return docs


def build_context(matches: List[dict]) -> str:
    """Format compact candidate context for LLM prompt (token-efficient)."""
    parts: List[str] = []
    max_desc_chars = 300

    for match in matches:
        if not match.get("entity_name"):
            continue

        score = _effective_retrieval_score(match)
        block = (
            f"Candidate Value Stream: {match.get('entity_name', '')}\n"
            f"Entity ID: {match.get('entity_id', '')}\n"
            f"Retrieval Score: {score:.4f}"
        )

        description = str(match.get("content") or "").strip()
        if not description:
            description = str(match.get("description") or "").strip()
        if description:
            if len(description) > max_desc_chars:
                description = description[:max_desc_chars].rstrip() + "..."
            block += f"\nDescription: {description}"

        parts.append(block)

    return "\n\n".join(parts)


# ----------------------------------------------------------------------
# Historical chunk provenance helpers
# ----------------------------------------------------------------------

def _parse_chunk_provenance(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}


def _range_label(value: Any, prefix: str) -> str:
    if isinstance(value, list) and value:
        nums = [x for x in value if isinstance(x, int)]
        if nums:
            if len(nums) == 1:
                return f"{prefix}-{nums[0]}"
            return f"{prefix}-{nums[0]}-{nums[-1]}"
    return ""


def _build_chunk_ref(chunk: dict) -> str:
    """
    Build a stable human-readable chunk reference from chunkProvenance.
    Prefer chunkId if present. Never emit page-?.
    """
    prov = _parse_chunk_provenance(chunk.get("chunkProvenance"))

    chunk_id = str(prov.get("chunkId") or "").strip()
    chunk_index = prov.get("chunkIndex")
    source_type = str(prov.get("sourceType") or "").strip()
    attachment_id = str(prov.get("attachmentId") or "").strip()

    page_label = _range_label(prov.get("pageRange"), "page")
    slide_label = _range_label(prov.get("slideRange"), "slide")

    parts: List[str] = []
    if attachment_id:
        parts.append(f"att-{attachment_id}")

    if source_type:
        parts.append(source_type)

    if page_label:
        parts.append(page_label)
    elif slide_label:
        parts.append(slide_label)

    if chunk_id:
        parts.append(chunk_id)
    elif chunk_index is not None:
        parts.append(f"chunk-{chunk_index}")

    if parts:
        return ":".join(parts)

    return str(chunk.get("id") or "").strip()


def _chunk_provenance_label(chunk: dict) -> str:
    prov = _parse_chunk_provenance(chunk.get("chunkProvenance"))
    page_label = _range_label(prov.get("pageRange"), "page")
    slide_label = _range_label(prov.get("slideRange"), "slide")
    source_type = str(prov.get("sourceType") or "").strip()

    parts = [p for p in [source_type, page_label or slide_label] if p]
    return ":".join(parts)


# ----------------------------------------------------------------------
# RAG: historical chunk retrieval
# ----------------------------------------------------------------------

def retrieve_historical_chunks(
    ppt_text: str,
    top_k_per_view: int = 12,
) -> List[dict]:
    """Retrieve similar historical IDMT chunks from idp_idmt_data."""

    client = AzureDirectSearchClient(index_name="idp_idmt_data")
    views = build_retrieval_views(ppt_text)

    select_fields = [
        "id",
        "sourceId",
        "title",
        "content",
        "contextKeywords",
        "chunkProvenance",
    ]

    all_matches: List[dict] = []

    for view in views:
        matches: List[dict] = []
        try:
            matches = client.search_hybrid(
                view,
                top_k=top_k_per_view,
                semantic_config="jira-chunks-semantic",
                use_semantic_rerank=True,
                select=select_fields,
            )
        except Exception:
            try:
                matches = client.search_vector(view, top_k=top_k_per_view, select=select_fields)
            except Exception:
                try:
                    matches = client.search_bm25(view, top_k=top_k_per_view, select=select_fields)
                except Exception:
                    matches = []

        all_matches.extend(matches or [])

    by_id: Dict[str, dict] = {}
    for match in all_matches:
        doc_id = str(match.get("id") or "").strip()
        if not doc_id:
            continue

        score = float(match.get("@search.reranker_score") or match.get("@search.score", 0.0) or 0.0)

        if doc_id not in by_id or score > by_id[doc_id]["_score"]:
            row = dict(match)
            row["_score"] = score
            row["_chunk_ref"] = _build_chunk_ref(row)
            row["_provenance_label"] = _chunk_provenance_label(row)
            by_id[doc_id] = row

    ranked = sorted(by_id.values(), key=lambda row: row.get("_score", 0.0), reverse=True)
    unique_tickets = {str(row.get("sourceId") or "") for row in ranked if row.get("sourceId")}

    logger.info("[RAG-RETRIEVE] %d chunks from %d tickets", len(ranked), len(unique_tickets))
    for idx, row in enumerate(ranked[:5], 1):
        logger.info(
            "[RAG-RETRIEVE]  #%d: ticket=%s score=%.4f title=%s",
            idx,
            row.get("sourceId", "?"),
            row.get("_score", 0.0),
            str(row.get("title", ""))[:80],
        )

    return ranked


# ----------------------------------------------------------------------
# RAG: Local ticket -> value stream mappings
# ----------------------------------------------------------------------

def load_ticket_vs_maps(base_dir: str | pathlib.Path | None = None) -> Dict[str, dict]:
    """Load 08_valuestream_map.json files from the prechunk output directory."""
    base = pathlib.Path(base_dir) if base_dir else PRECHUNK_DIR
    mapping: Dict[str, dict] = {}

    for path in sorted(glob.glob(str(base / "**" / "08_valuestream_map.json"))):
        with open(path, encoding="utf-8") as f:
            record = json.load(f)

        ticket_id = str(record.get("ticketId") or "").strip()
        if ticket_id:
            mapping[ticket_id] = record

    with_vs = sum(1 for row in mapping.values() if row.get("valueStreamNames"))
    logger.info(
        "[RAG-MAPS] %d maps loaded; %d with VS, %d empty",
        len(mapping),
        with_vs,
        len(mapping) - with_vs,
    )

    if with_vs == 0 and mapping:
        logger.warning("[RAG-MAPS] *** ALL ticket VS maps are EMPTY ***")

    return mapping


# ----------------------------------------------------------------------
# RAG: map retrieved chunks -> ticket VS evidence
# ----------------------------------------------------------------------

def build_historical_vs_evidence(
    chunks: List[dict],
    ticket_vs_map: Dict[str, dict],
    max_ticket_hits: int = 20,
) -> Tuple[List[dict], List[dict]]:
    """Map retrieved chunks -> ticket VS assignments -> support counts."""
    ticket_hits: Dict[str, dict] = {}

    for chunk in chunks:
        ticket_id = str(chunk.get("sourceId") or "").strip()
        if not ticket_id:
            continue

        score = float(chunk.get("_score", 0.0) or 0.0)
        raw_doc_id = str(chunk.get("id") or "").strip()
        chunk_ref = str(chunk.get("_chunk_ref") or _build_chunk_ref(chunk)).strip()

        if ticket_id not in ticket_hits:
            ticket_hits[ticket_id] = {
                "ticket_id": ticket_id,
                "best_score": score,
                "matched_chunk_ids": [chunk_ref] if chunk_ref else [],
                "matched_doc_ids": [raw_doc_id] if raw_doc_id else [],
                "title": chunk.get("title", ""),
            }
        else:
            ticket_hits[ticket_id]["best_score"] = max(ticket_hits[ticket_id]["best_score"], score)

            if chunk_ref and chunk_ref not in ticket_hits[ticket_id]["matched_chunk_ids"]:
                ticket_hits[ticket_id]["matched_chunk_ids"].append(chunk_ref)

            if raw_doc_id and raw_doc_id not in ticket_hits[ticket_id]["matched_doc_ids"]:
                ticket_hits[ticket_id]["matched_doc_ids"].append(raw_doc_id)

    ranked_tickets = sorted(
        ticket_hits.values(),
        key=lambda row: row["best_score"],
        reverse=True,
    )[:max_ticket_hits]

    support_by_vs: Dict[str, dict] = {}
    for hit in ranked_tickets:
        vs_record = ticket_vs_map.get(hit["ticket_id"])
        if not vs_record:
            continue

        names = vs_record.get("valueStreamNames") or []
        ids = vs_record.get("valueStreamIds") or []
        statuses = vs_record.get("valueStreamStatuses") or []
        jira_theme_ids = vs_record.get("jiraThemeIds") or []

        for idx, name in enumerate(names):
            vs_name = str(name or "").strip()
            if not vs_name:
                continue

            if vs_name not in support_by_vs:
                support_by_vs[vs_name] = {
                    "entity_name": vs_name,
                    "entity_id": ids[idx] if idx < len(ids) else "",
                    "source_theme_id": jira_theme_ids[idx] if idx < len(jira_theme_ids) else "",
                    "statuses": [],
                    "support_count": 0,
                    "supporting_ticket_ids": [],
                    "supporting_chunk_ids": [],
                    "supporting_doc_ids": [],
                    "best_support_score": 0.0,
                }

            entry = support_by_vs[vs_name]
            entry["support_count"] += 1
            entry["best_support_score"] = max(entry["best_support_score"], hit["best_score"])

            if hit["ticket_id"] not in entry["supporting_ticket_ids"]:
                entry["supporting_ticket_ids"].append(hit["ticket_id"])

            for cid in hit["matched_chunk_ids"][:5]:
                if cid and cid not in entry["supporting_chunk_ids"]:
                    entry["supporting_chunk_ids"].append(cid)

            for did in hit.get("matched_doc_ids", [])[:5]:
                if did and did not in entry["supporting_doc_ids"]:
                    entry["supporting_doc_ids"].append(did)

            if idx < len(statuses):
                status = str(statuses[idx] or "").strip()
                if status and status not in entry["statuses"]:
                    entry["statuses"].append(status)

    vs_support = sorted(
        support_by_vs.values(),
        key=lambda row: (row["support_count"], row["best_support_score"]),
        reverse=True,
    )

    logger.info("[RAG-EVIDENCE] %d ticket hits -> %d VS support entries", len(ranked_tickets), len(vs_support))
    for row in vs_support[:5]:
        logger.info(
            "[RAG-EVIDENCE] %s count=%d score=%.4f",
            row["entity_name"],
            row["support_count"],
            row["best_support_score"],
        )

    return ranked_tickets, vs_support


# ----------------------------------------------------------------------
# RAG: KG candidate retrieval
# ----------------------------------------------------------------------

def retrieve_kg_candidates(
    query_text: str,
    top_k: int = 50,
    allowed_names: Optional[List[str]] = None,
) -> List[dict]:
    """Retrieve ValueStream entities from idp_kg_data_test."""
    client = AzureDirectSearchClient(index_name="idp_kg_data_test")
    vs_filter = "node_type eq 'ValueStream'"

    try:
        matches = client.search_vector(
            query_text,
            top_k=top_k,
            filter_expression=vs_filter,
        )
    except Exception:
        try:
            matches = client.search_vector(query_text, top_k=top_k, filter_expression=vs_filter)
        except Exception:
            try:
                matches = client.search_bm25(query_text, top_k=top_k, filter_expression=vs_filter)
            except Exception:
                matches = []

    allowed_norm = None
    if allowed_names:
        allowed_norm = {normalize_for_search(str(name)) for name in allowed_names if str(name).strip()}

    dedup: Dict[str, dict] = {}
    for match in matches:
        name = str(match.get("entity_name") or "").strip()
        entity_id = str(match.get("entity_id") or "").strip()
        if not name:
            continue

        name_norm = normalize_for_search(name)
        if allowed_norm is not None and name_norm not in allowed_norm:
            continue

        key = entity_id or name_norm
        score = float(match.get("@search.reranker_score") or match.get("@search.score", 0.0) or 0.0)

        if key in dedup and dedup[key].get("score", 0.0) >= score:
            continue

        props = match.get("properties")
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        if not isinstance(props, dict):
            props = {}

        dedup[key] = {
            "entity_id": entity_id,
            "entity_name": name,
            "description": str(match.get("content") or ""),
            "value_proposition": str(props.get("value_stream_value_proposition") or ""),
            "trigger": str(props.get("value_stream_trigger") or ""),
            "score": round(score, 4),
        }

    candidates = sorted(dedup.values(), key=lambda row: row.get("score", 0.0), reverse=True)
    logger.info("[RAG-KG] %d KG candidates retrieved", len(candidates))

    for idx, row in enumerate(candidates[:10], 1):
        logger.info(
            "[RAG-KG]   #%d: %s (score=%.4f)",
            idx,
            row["entity_name"],
            row.get("score", 0.0),
        )

    if allowed_names:
        existing = {normalize_for_search(row["entity_name"]) for row in candidates if row.get("entity_name")}
        for name in allowed_names:
            clean_name = str(name).strip()
            if clean_name and normalize_for_search(clean_name) not in existing:
                candidates.append(
                    {
                        "entity_id": "",
                        "entity_name": clean_name,
                        "description": "",
                        "value_proposition": "",
                        "trigger": "",
                        "score": 0.0,
                    }
                )

    return candidates


# ----------------------------------------------------------------------
# LLM output sanitization
# ----------------------------------------------------------------------

def sanitize_selected(parsed: dict, candidates: List[dict]) -> dict:
    """Match LLM-selected VS names back to valid candidates."""
    logger.info(
        "[SANITIZE] %d selections vs %d candidates",
        len(parsed.get("selected_value_streams") or []),
        len(candidates),
    )

    by_name = {c.get("entity_name", "").strip().lower(): c for c in candidates if c.get("entity_name")}
    by_id = {
        str(c.get("entity_id") or "").strip().lower(): c
        for c in candidates
        if str(c.get("entity_id") or "").strip()
    }

    def _norm(text: str) -> str:
        text = re.sub(r"[^a-z0-9\s]", " ", (text or "").lower().strip())
        return re.sub(r"\s+", " ", text).strip()

    by_norm = {_norm(c["entity_name"]): c for c in candidates if c.get("entity_name")}

    selected: List[dict] = []
    for row in parsed.get("selected_value_streams") or []:
        raw_name = str(row.get("entity_name") or row.get("name") or "").strip()
        raw_id = str(row.get("entity_id") or "").strip().lower()

        if not raw_name:
            continue

        candidate = None
        method = "none"

        if raw_id and raw_id in by_id:
            candidate = by_id[raw_id]
            method = "entity_id"

        if candidate is None and raw_name.lower() in by_name:
            candidate = by_name[raw_name.lower()]
            method = "exact_name"

        if candidate is None and _norm(raw_name) in by_norm:
            candidate = by_norm[_norm(raw_name)]
            method = "normalized"

        if candidate is None:
            best = None
            best_score = 0.0
            for cand in candidates:
                candidate_name = str(cand.get("entity_name") or "").strip()
                if not candidate_name:
                    continue

                score = SequenceMatcher(None, _norm(raw_name), _norm(candidate_name)).ratio()
                if score > best_score:
                    best_score = score
                    best = cand

            if best and best_score >= 0.75:
                candidate = best
                method = f"fuzzy({best_score:.2f})"

        if candidate is None:
            logger.warning("[SANITIZE] DROPPED: '%s'", raw_name)
            continue

        logger.info(
            "[SANITIZE] KEPT: '%s' -> '%s' via %s",
            raw_name,
            candidate.get("entity_name", "?"),
            method,
        )

        selected.append(
            {
                "entity_id": row.get("entity_id") or candidate.get("entity_id", ""),
                "entity_name": candidate.get("entity_name", raw_name),
                "confidence": float(row.get("confidence", 0.0) or 0.0),
                "reason": str(row.get("reason") or ""),
                "supporting_ticket_ids": row.get("supporting_ticket_ids") or [],
                "supporting_chunk_ids": row.get("supporting_chunk_ids") or [],
            }
        )

    parsed["selected_value_streams"] = selected
    parsed["rejected_candidates"] = parsed.get("rejected_candidates") or []

    logger.info(
        "[SANITIZE] Result: %d kept, %d rejected",
        len(selected),
        len(parsed["rejected_candidates"]),
    )

    return parsed
