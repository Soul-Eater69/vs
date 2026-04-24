from __future__ import annotations

from typing import List

from ingestion.application.prompts import (
    build_historical_selection_prompt,
    build_historical_selection_system_prompt,
)


def build_system_prompt() -> str:
    return build_historical_selection_system_prompt(
        min_select=6,
        max_select=12,
    )


def build_candidate_prompt(query_for_prompt: str, candidates: List[dict]) -> str:
    blocks = []
    for idx, row in enumerate(candidates, start=1):
        bucket = str(row.get("bucket") or "").strip()
        lines = [
            f"{idx}. {row.get('entity_name', '')}",
            f"Entity ID: {row.get('entity_id', '')}",
            f"Evidence bucket: {bucket or 'unknown'}",
        ]

        description = str(row.get("description") or "").strip()
        if description:
            lines.append(f"Description: {description[:320]}")

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

            reasons = [str(text).strip() for text in (row.get("historical_reasons") or []) if str(text).strip()]
            if reasons:
                direct_reasons = [reason for reason in reasons if "/ direct]" in reason]
                implied_reasons = [reason for reason in reasons if "/ implied]" in reason]
                ordered = (direct_reasons + implied_reasons)[:3]
                lines.append("Analog evidence:")
                for reason in ordered:
                    lines.append(f"  - {reason}")

        blocks.append("\n".join(lines))

    return build_historical_selection_prompt(
        query_for_prompt=query_for_prompt,
        candidate_blocks="\n\n".join(blocks),
    )
