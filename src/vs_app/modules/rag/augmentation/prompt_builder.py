from __future__ import annotations

import re
from typing import List

from vs_app.modules.prompts.loader import (
    build_historical_selection_prompt,
    build_historical_selection_system_prompt,
)


def build_system_prompt(min_select: int = 4, max_select: int = 12) -> str:
    return build_historical_selection_system_prompt(
        min_select=min_select,
        max_select=max_select,
    )


def build_candidate_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    return build_direct_candidate_prompt(query_for_prompt, candidates)


def build_direct_candidate_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    ordered_candidates = _order_candidates_for_llm(candidates)
    candidate_blocks = _candidate_blocks(ordered_candidates)

    return build_historical_selection_prompt(
        query_for_prompt=query_for_prompt,
        candidate_blocks=candidate_blocks,
    )


def build_historical_gap_prompt(
    query_for_prompt: str,
    candidates: List[dict],
    precedent_candidates: List[dict] | None = None,
) -> str:
    ordered_candidates = _order_candidates_for_llm(candidates)
    candidate_blocks = _candidate_blocks(ordered_candidates)
    precedent_context = _precedent_context(precedent_candidates or candidates)

    return f"""IDEA CARD:

{query_for_prompt}

TASK:
You are doing a separate historical pattern adjudication pass.
Semantic retrieval already handled direct wording matches. Your job is to decide whether
historical-supported candidates represent value streams implied or confirmed by repeatable
patterns in similar past tickets.

Historical candidates are not expected to have strong literal wording overlap with the idea card.
Judge whether the analog evidence shows the same downstream operational work, business lifecycle,
or recurring capability pattern.

The precedent section groups evidence by prior similar ticket. Treat that bundle view as important:
if one close prior ticket supported several value streams together, that can reveal a reusable
business pattern that individual candidate snippets may hide.

CALIBRATION EXAMPLES:
Positive pattern-induced selection:
- If the current idea describes network steering, tiering, payment, invoice, utilization, or member
  operations only indirectly, and multiple similar historical tickets carried a downstream value
  stream with coherent analog summaries, include that value stream as an implied gap-fill.
- Example decision: include "Manage Invoice and Payment Receipt" when several similar tickets show
  recurring billing/payment receipt work even if the current card does not literally say "invoice".

Negative generic false positive:
- Do not include "Align and Execute IT Strategy" merely because the card mentions a roadmap,
  strategy, modernization, or phased plan. Include it only when the work is actually IT strategy
  execution rather than business capability delivery.
- Do not include generic enterprise/process streams when the analog summaries are from a different
  operational domain.

SELECTION RULES:
- Evaluate every historical candidate independently.
- Include when repeated analog evidence shows a defensible implied value stream.
- Multiple coherent implied analogs can be enough; direct Jira-linked analogs are stronger.
- Exclude one-off, generic, or domain-mismatched analogs even when scores are high.
- Prefer concise reasons that cite the analog pattern, not generic score language.

SIMILAR HISTORICAL PRECEDENTS:

{precedent_context}

HISTORICAL-SUPPORTED CANDIDATES:

{candidate_blocks}"""


def _candidate_blocks(ordered_candidates: List[dict]) -> str:
    blocks = []
    for idx, row in enumerate(ordered_candidates, start=1):
        bucket = str(row.get("bucket") or "").strip()
        lane = str(row.get("candidate_lane") or "").strip()
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Evidence bucket: {bucket or 'unknown'}",
        ]
        if lane:
            lines.append(f"Candidate lane: {lane}")

        description = str(row.get("description") or "").strip()
        if description:
            lines.append(f"Description: {description[:320]}")

        lane_guidance = _lane_guidance(lane)
        if lane_guidance:
            lines.append(f"Lane guidance: {lane_guidance}")

        if row.get("from_semantic"):
            lines.append(f"Semantic score: {float(row.get('semantic_score', 0.0) or 0.0):.4f}")

        if row.get("from_historical"):
            direct_count = int(row.get("direct_count", 0) or 0)
            implied_count = int(row.get("implied_count", 0) or 0)
            lines.append(
                "Historical support: "
                f"{int(row.get('support_count', 0) or 0)} tickets "
                f"({direct_count} direct, {implied_count} implied), "
                f"best similarity {float(row.get('best_support_score', 0.0) or 0.0):.4f}, "
                f"average similarity {float(row.get('avg_support_score', 0.0) or 0.0):.4f}"
            )
            if lane == "historical_recall":
                lines.append(
                    "Historical role: implied gap-fill candidate. Semantic retrieval likely missed this,"
                    " so judge it by whether the analog pattern suggests an unstated but materially"
                    " relevant value stream."
                )

        overlaps = _overlap_candidates(row, ordered_candidates)
        if overlaps:
            lines.append(f"Potential overlap/conflict: {', '.join(overlaps[:3])}")

        reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
        if reasons:
            direct_reasons = [reason for reason in reasons if "/ direct]" in reason]
            implied_reasons = [reason for reason in reasons if "/ implied]" in reason]
            ordered = (direct_reasons + implied_reasons)[:3]
            lines.append("Analog evidence:")
            for reason in ordered:
                lines.append(f"  - {reason}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _precedent_context(candidates: List[dict], *, limit: int = 8) -> str:
    precedents: dict[str, dict] = {}
    for row in candidates:
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue

        parsed_any = False
        for reason in [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]:
            parsed = _parse_analog_reason(reason)
            if not parsed:
                continue
            parsed_any = True
            ticket_id, inference_type, snippet = parsed
            _add_precedent(precedents, ticket_id, name, inference_type, snippet)

        if parsed_any:
            continue

        for ticket_id in [str(value).strip() for value in (row.get("supporting_ticket_ids") or []) if str(value).strip()]:
            inference_type = "direct" if int(row.get("direct_count", 0) or 0) else "implied"
            _add_precedent(precedents, ticket_id, name, inference_type, "")

    if not precedents:
        return "No grouped precedent context was available; rely on candidate-level analog evidence."

    rows = sorted(
        precedents.values(),
        key=lambda item: (
            len(item["candidates"]),
            len(item["direct_candidates"]),
            len(item["snippets"]),
        ),
        reverse=True,
    )

    blocks: List[str] = []
    for idx, item in enumerate(rows[:limit], start=1):
        direct = sorted(item["direct_candidates"])
        implied = sorted(item["implied_candidates"])
        candidate_parts = []
        if direct:
            candidate_parts.append("direct: " + ", ".join(direct[:8]))
        if implied:
            candidate_parts.append("implied: " + ", ".join(implied[:8]))
        lines = [
            f"{idx}. {item['ticket_id']}",
            f"   Pattern-supported value streams: {'; '.join(candidate_parts) if candidate_parts else 'none listed'}",
        ]
        for snippet in item["snippets"][:2]:
            lines.append(f"   Analog summary: {snippet[:260]}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _add_precedent(
    precedents: dict[str, dict],
    ticket_id: str,
    candidate_name: str,
    inference_type: str,
    snippet: str,
) -> None:
    ticket_key = ticket_id.strip()
    if not ticket_key:
        return
    entry = precedents.setdefault(
        ticket_key,
        {
            "ticket_id": ticket_key,
            "candidates": set(),
            "direct_candidates": set(),
            "implied_candidates": set(),
            "snippets": [],
        },
    )
    entry["candidates"].add(candidate_name)
    target = "direct_candidates" if inference_type == "direct" else "implied_candidates"
    entry[target].add(candidate_name)
    clean_snippet = " ".join((snippet or "").split())
    if clean_snippet and clean_snippet not in entry["snippets"]:
        entry["snippets"].append(clean_snippet)


def _parse_analog_reason(reason: str) -> tuple[str, str, str] | None:
    match = re.match(r"^\[([^/\]]+)\s*/\s*(direct|implied)\]\s*(.*)$", reason, re.IGNORECASE)
    if not match:
        return None
    ticket_id = match.group(1).strip()
    inference_type = match.group(2).strip().lower()
    snippet = match.group(3).strip()
    return ticket_id, inference_type, snippet


def _order_candidates_for_llm(candidates: List[dict]) -> List[dict]:
    lane_priority = {
        "confirmed_direct": 0,
        "historical_recall": 1,
        "semantic_direct": 2,
    }
    return sorted(
        list(candidates),
        key=lambda row: (
            lane_priority.get(str(row.get("candidate_lane") or ""), 9),
            -float(row.get("ranking_score", 0.0) or 0.0),
        ),
    )


def _lane_guidance(lane: str) -> str:
    if lane == "confirmed_direct":
        return "Strong direct candidate with both semantic and historical support."
    if lane == "historical_recall":
        return (
            "Gap-fill lane. This candidate may be materially relevant even without explicit wording"
            " overlap if repeated analog evidence is coherent."
        )
    if lane == "semantic_direct":
        return "Direct semantic lane. Keep only if the business fit is genuinely material."
    return ""


def _overlap_candidates(row: dict, candidates: List[dict]) -> List[str]:
    current_name = str(row.get("entity_name") or "").strip()
    current_tokens = _name_tokens(current_name)
    overlaps: List[str] = []
    for other in candidates:
        other_name = str(other.get("entity_name") or "").strip()
        if not other_name or other_name == current_name:
            continue
        other_tokens = _name_tokens(other_name)
        shared = current_tokens & other_tokens
        if len(shared) >= 2:
            overlaps.append(other_name)
    return overlaps


def _name_tokens(name: str) -> set[str]:
    stop_words = {"and", "for", "the", "to", "of", "in"}
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", (name or "").lower())
        if token and token not in stop_words
    }
    return tokens
