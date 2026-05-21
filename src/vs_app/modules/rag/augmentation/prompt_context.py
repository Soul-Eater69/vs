from __future__ import annotations

import re
from typing import Iterable, List

from vs_app.modules.prompts.loader import (
    build_historical_gap_selection_prompt,
    build_historical_selection_prompt,
    build_historical_selection_system_prompt,
    build_review_pool_selection_prompt,
    build_review_pool_selection_system_prompt,
)


def build_system_prompt(min_select: int = 4, max_select: int = 12) -> str:
    return build_historical_selection_system_prompt(
        min_select=min_select,
        max_select=max_select,
    )


def build_direct_candidate_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    return build_historical_selection_prompt(
        query_for_prompt=query_for_prompt,
        candidate_blocks=format_candidate_blocks(candidates),
    )


def build_historical_gap_prompt(
    query_for_prompt: str,
    candidates: List[dict],
    precedent_candidates: List[dict] | None = None,
    historical_ticket_hits: List[dict] | None = None,
) -> str:
    return build_historical_gap_selection_prompt(
        query_for_prompt=query_for_prompt,
        precedent_context=format_historical_precedents(precedent_candidates or candidates),
        faiss_hit_context=format_historical_ticket_hits(historical_ticket_hits or []),
        candidate_blocks=format_candidate_blocks(candidates),
    )


def build_review_pool_system_prompt(max_select: int) -> str:
    return build_review_pool_selection_system_prompt(max_select=max_select)


def build_review_pool_candidate_prompt(
    query_for_prompt: str,
    candidates: List[dict],
    final_output_count: int,
    prompt_budget: dict | None = None,
) -> str:
    budget = prompt_budget or {}
    idea_chars = int(budget.get("idea_card_prompt_chars", 2200))
    return build_review_pool_selection_prompt(
        query_for_prompt=(query_for_prompt or "")[:idea_chars],
        requested_final_output_count=final_output_count,
        candidate_blocks=format_review_pool_candidate_blocks(
            candidates,
            prompt_budget=budget,
        ),
    )


def format_candidate_blocks(candidates: List[dict]) -> str:
    blocks = []
    for idx, row in enumerate(order_candidates(candidates), start=1):
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Lane: {_lane(row)}",
        ]

        description = str(row.get("description") or "").strip()
        if description:
            lines.append(f"Description: {description[:320]}")

        if row.get("from_semantic"):
            lines.append(f"Semantic score: {float(row.get('semantic_score', 0.0) or 0.0):.4f}")

        if row.get("from_historical"):
            lines.extend(_historical_support_lines(row))

        analogs = _ordered_analog_reasons(row.get("historical_reasons") or [])
        if analogs:
            lines.append("Analog evidence:")
            lines.extend(f"  - {reason}" for reason in analogs[:3])

        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def format_review_pool_candidate_blocks(
    candidates: List[dict],
    *,
    prompt_budget: dict | None = None,
) -> str:
    budget = prompt_budget or {}
    desc_chars = int(budget.get("candidate_description_chars", 160))
    analog_limit = int(budget.get("analogs_per_candidate", 2))
    analog_chars = int(budget.get("analog_chars", 140))
    ticket_limit = int(budget.get("historical_ticket_ids_per_candidate", 3))

    # Preserve the order produced by candidate_merger.py — it already prioritizes
    # merged candidates first, then evidence-qualified historical-only, then strong
    # semantic-only. Re-ordering here would undo that logic.
    blocks = []
    for idx, row in enumerate(candidates, start=1):
        lane = _lane(row)
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Lane: {lane}",
        ]

        description = " ".join(str(row.get("description") or "").split())
        if description:
            lines.append(f"Description: {description[:desc_chars]}")

        if row.get("from_semantic"):
            lines.append(f"Semantic score: {float(row.get('semantic_score', 0.0) or 0.0):.4f}")

        if row.get("from_historical"):
            supporting_ticket_count = int(
                row.get("supporting_ticket_count", row.get("support_count", 0)) or 0
            )
            direct_count = int(row.get("direct_count", 0) or 0)
            implied_count = int(row.get("implied_count", 0) or 0)
            best = float(row.get("best_support_score", 0.0) or 0.0)
            avg = float(row.get("avg_support_score", 0.0) or 0.0)
            weighted = float(
                row.get("weighted_support", row.get("weighted_support_count", 0.0)) or 0.0
            )
            tickets = _text_list(row.get("supporting_ticket_ids") or [])[:ticket_limit]

            lines.append(
                f"Historical: {supporting_ticket_count} tickets "
                f"({direct_count} direct, {implied_count} implied), "
                f"best {best:.3f}, avg {avg:.3f}, weighted {weighted:.3f}"
            )
            if tickets:
                lines.append(f"Supporting tickets: {', '.join(tickets)}")

            evidence_rows = _ordered_historical_evidence(row.get("historical_evidence") or [])[:analog_limit]
            analogs = _ordered_analog_reasons(row.get("historical_reasons") or [])[:analog_limit]
            if evidence_rows:
                lines.append("Evidence:")
                for evidence in evidence_rows:
                    lines.extend(_format_historical_evidence(evidence, analog_chars))
            elif analogs:
                lines.append("Evidence:")
                for reason in analogs:
                    clean = " ".join(str(reason).split())
                    lines.append(f"- {clean[:analog_chars]}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def format_historical_precedents(candidates: List[dict], *, limit: int = 8) -> str:
    precedents: dict[str, dict] = {}
    for row in candidates:
        name = str(row.get("entity_name") or "").strip()
        if not name:
            continue

        parsed_reasons = [
            parsed
            for reason in _text_list(row.get("historical_reasons") or [])
            if (parsed := parse_analog_reason(reason))
        ]
        if parsed_reasons:
            for ticket_id, inference_type, snippet in parsed_reasons:
                add_precedent(precedents, ticket_id, name, inference_type, snippet)
            continue

        inference_type = "direct" if int(row.get("direct_count", 0) or 0) else "implied"
        for ticket_id in _text_list(row.get("supporting_ticket_ids") or []):
            add_precedent(precedents, ticket_id, name, inference_type, "")

    if not precedents:
        return "See historical ticket context and candidate-level analog evidence below."

    rows = sorted(
        precedents.values(),
        key=lambda item: (
            len(item["candidates"]),
            len(item["direct_candidates"]),
            len(item["snippets"]),
        ),
        reverse=True,
    )
    return "\n\n".join(format_precedent_block(idx, item) for idx, item in enumerate(rows[:limit], start=1))


def format_historical_ticket_hits(ticket_hits: List[dict], *, limit: int = 10) -> str:
    if not ticket_hits:
        return "No historical ticket context was available; rely on candidate-level analog evidence."

    blocks = []
    for idx, hit in enumerate(ticket_hits[:limit], start=1):
        ticket_id = str(hit.get("ticket_id") or "").strip() or f"hit-{idx}"
        title = str(hit.get("title") or "").strip()
        direct_names = _text_list(hit.get("direct_vs_names") or [])
        implied_names = _text_list(hit.get("implied_vs_names") or [])
        fallback_names = _text_list(hit.get("value_stream_names") or hit.get("value_stream_labels") or [])
        if not direct_names and not implied_names and fallback_names:
            implied_names = fallback_names

        lines = [
            f"H{idx}. {ticket_id}{f' - {title}' if title and title != ticket_id else ''}",
            f"   Similarity: {float(hit.get('best_score', 0.0) or 0.0):.4f}",
        ]
        label_source = str(hit.get("label_source") or "").strip()
        if label_source:
            lines.append(f"   Label source: {label_source}")
        lines.append(f"   Direct value streams: {', '.join(direct_names[:10]) if direct_names else 'none listed'}")
        lines.append(f"   Implied value streams: {', '.join(implied_names[:10]) if implied_names else 'none listed'}")

        direct_functions = _text_list(hit.get("direct_functions_canonical") or [])
        implied_functions = _text_list(hit.get("implied_functions_canonical") or [])
        if direct_functions:
            lines.append(f"   Direct functions: {', '.join(direct_functions[:8])}")
        if implied_functions:
            lines.append(f"   Implied functions: {', '.join(implied_functions[:8])}")

        summary = " ".join(str(hit.get("summary_preview") or "").split())
        if summary:
            lines.append(f"   Ticket summary: {summary[:420]}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def format_precedent_block(idx: int, item: dict) -> str:
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
    lines.extend(f"   Analog summary: {snippet[:260]}" for snippet in item["snippets"][:2])
    return "\n".join(lines)


def add_precedent(
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


def parse_analog_reason(reason: str) -> tuple[str, str, str] | None:
    match = re.match(r"^\[([^/\]]+)\s*/\s*(direct|implied)\]\s*(.*)$", reason, re.IGNORECASE)
    if not match:
        return None
    return (
        match.group(1).strip(),
        match.group(2).strip().lower(),
        match.group(3).strip(),
    )


def order_candidates(candidates: List[dict]) -> List[dict]:
    lane_priority = {
        "semantic_plus_historical": 0,
        "semantic_only": 1,
        "historical_only": 2,
    }
    return sorted(
        list(candidates),
        key=lambda row: (
            lane_priority.get(_lane(row), 9),
            -float(row.get("semantic_score", 0.0) or 0.0),
            -float(row.get("best_support_score", 0.0) or 0.0),
            -float(row.get("weighted_support", row.get("weighted_support_count", 0.0)) or 0.0),
            str(row.get("entity_name") or "").lower(),
        ),
    )


def _historical_support_lines(row: dict, *, prefix: str = "Historical support") -> List[str]:
    direct_count = int(row.get("direct_count", 0) or 0)
    implied_count = int(row.get("implied_count", 0) or 0)
    supporting_ticket_count = int(row.get("supporting_ticket_count", row.get("support_count", 0)) or 0)
    supporting_ticket_ids = _text_list(row.get("supporting_ticket_ids") or [])
    weighted_support = float(row.get("weighted_support", row.get("weighted_support_count", 0.0)) or 0.0)
    lines = [
        (
            f"{prefix}: {supporting_ticket_count} unique tickets "
            f"({direct_count} direct, {implied_count} implied)"
        ),
        f"Best historical similarity: {float(row.get('best_support_score', 0.0) or 0.0):.4f}",
        f"Average historical similarity: {float(row.get('avg_support_score', 0.0) or 0.0):.4f}",
    ]
    if weighted_support:
        lines.append(f"Weighted historical support: {weighted_support:.4f}")
    if supporting_ticket_ids:
        lines.append(f"Supporting tickets: {', '.join(supporting_ticket_ids[:5])}")
    return [
        *lines
    ]


def _ordered_historical_evidence(values: Iterable[object]) -> List[dict]:
    rows = [dict(value) for value in values if isinstance(value, dict)]
    direct_rows = [row for row in rows if str(row.get("inference_type") or "").lower() == "direct"]
    implied_rows = [row for row in rows if str(row.get("inference_type") or "").lower() == "implied"]
    other_rows = [row for row in rows if row not in direct_rows and row not in implied_rows]
    return direct_rows + implied_rows + other_rows


def _format_historical_evidence(row: dict, chars: int) -> List[str]:
    ticket_id = str(row.get("ticket_id") or "").strip() or "historical ticket"
    inference_type = str(row.get("inference_type") or "").strip().lower() or "support"
    title = _clip_text(row.get("title"), min(max(chars, 60), 140))
    reason = _clip_text(row.get("reason"), chars)
    summary = _clip_text(row.get("summary_preview"), chars)
    direct_functions = _text_list(row.get("direct_functions_canonical") or [])[:3]
    implied_functions = _text_list(row.get("implied_functions_canonical") or [])[:3]

    header = f"- {ticket_id} / {inference_type}"
    if title and title.lower() != ticket_id.lower():
        header = f"{header}: {title}"

    lines = [header]
    if reason:
        lines.append(f"  Why this stream: {reason}")
    if summary:
        lines.append(f"  Prior ticket summary: {summary}")

    function_parts = []
    if direct_functions:
        function_parts.append("direct functions: " + ", ".join(direct_functions))
    if implied_functions:
        function_parts.append("implied functions: " + ", ".join(implied_functions))
    if function_parts:
        lines.append(f"  Supporting functions: {'; '.join(function_parts)}")
    return lines


def _clip_text(value: object, chars: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    return text[: max(1, int(chars or 1))]


def _ordered_analog_reasons(values: Iterable[object]) -> List[str]:
    reasons = _text_list(values)
    direct_reasons = [reason for reason in reasons if "/ direct]" in reason]
    implied_reasons = [reason for reason in reasons if "/ implied]" in reason]
    other_reasons = [
        reason
        for reason in reasons
        if reason not in direct_reasons and reason not in implied_reasons
    ]
    return direct_reasons + implied_reasons + other_reasons


def _text_list(values: Iterable[object]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    if isinstance(values, str):
        iterable = [values]
    elif isinstance(values, Iterable):
        iterable = values
    else:
        iterable = []
    for value in iterable:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _lane(row: dict) -> str:
    return str(row.get("lane") or row.get("candidate_lane") or row.get("bucket") or "").strip() or "unknown"
