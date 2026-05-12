from __future__ import annotations

from typing import Iterable, List

from vs_app.modules.prompts.loader import (
    build_review_pool_selection_prompt,
    build_review_pool_selection_system_prompt,
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

    blocks = []
    for idx, row in enumerate(candidates, start=1):
        lane = _lane(row)
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Lane: {lane}",
        ]

        if row.get("foundational_signal"):
            match_type = str(row.get("foundational_match_type") or "match").strip()
            match_text = str(row.get("foundational_match_text") or "").strip()
            if match_text:
                lines.append(f'Foundational signal: {match_type} match to "{match_text}"')
            else:
                lines.append("Foundational signal: matched current-card value-stream signal")

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

            analogs = _ordered_analog_reasons(row.get("historical_reasons") or [])[:analog_limit]
            if analogs:
                lines.append("Evidence:")
                for reason in analogs:
                    clean = " ".join(str(reason).split())
                    lines.append(f"- {clean[:analog_chars]}")

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


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
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _lane(row: dict) -> str:
    return str(row.get("lane") or row.get("candidate_lane") or row.get("bucket") or "").strip() or "unknown"
