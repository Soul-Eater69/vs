"""Write single JSON audit output file."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import AuditOutput, AuditRunMetadata, AuditSummary, TicketAuditRecord


def _build_summary(records: list[TicketAuditRecord]) -> AuditSummary:
    presence_final: Counter = Counter()
    presence_fuzzy: Counter = Counter()
    presence_llm: Counter = Counter()
    link_access_counts: Counter = Counter()
    link_usefulness_reason_counts: Counter = Counter()
    att_ext_counts: Counter = Counter()
    comparison_counts: Counter = Counter(
        exact_match=0,
        compatible_match=0,
        major_disagreement=0,
        fuzzy_false_negative_proxy=0,
        fuzzy_false_positive_proxy=0,
        needs_manual_review=0,
    )

    total_links = 0
    useful_links = 0
    not_useful_links = 0
    accessible_links = 0
    inaccessible_links = 0

    for rec in records:
        presence_final[rec.final_recommendation.presence] += 1
        presence_fuzzy[rec.fuzzy_decision.presence] += 1
        if rec.llm_decision:
            presence_llm[rec.llm_decision.presence] += 1

        total_links += rec.link_summary.total_links
        useful_links += rec.link_summary.useful_links
        not_useful_links += rec.link_summary.not_useful_links
        accessible_links += rec.link_summary.accessible_links
        inaccessible_links += rec.link_summary.inaccessible_links

        for lnk in rec.links:
            link_access_counts[lnk.access_status.value] += 1
            reason = (
                lnk.usefulness_reason_llm
                if lnk.is_useful_llm is not None
                else lnk.usefulness_reason_fuzzy
            ) or "unknown"
            link_usefulness_reason_counts[reason[:60]] += 1

        for att in rec.attachments:
            if att.extension:
                att_ext_counts[att.extension] += 1

        if rec.comparison:
            cmp = rec.comparison
            comparison_counts[cmp.presence_match_type] += 1
            if cmp.is_fuzzy_false_negative_proxy:
                comparison_counts["fuzzy_false_negative_proxy"] += 1
            if cmp.is_fuzzy_false_positive_proxy:
                comparison_counts["fuzzy_false_positive_proxy"] += 1
            if cmp.needs_manual_review:
                comparison_counts["needs_manual_review"] += 1
        elif rec.llm_decision is None:
            comparison_counts["llm_skipped"] = comparison_counts.get("llm_skipped", 0) + 1

    total = len(records)
    llm_compared = sum(1 for r in records if r.comparison is not None)

    exact = comparison_counts["exact_match"]
    compatible = comparison_counts["compatible_match"]
    major = comparison_counts["major_disagreement"]
    denom = llm_compared or 1

    proxy_accuracy = {
        "tickets_with_llm": llm_compared,
        "exact_match_rate": round(exact / denom, 3),
        "compatible_match_rate": round(compatible / denom, 3),
        "major_disagreement_rate": round(major / denom, 3),
    }

    return AuditSummary(
        total_tickets=total,
        ticket_presence_counts_final=dict(presence_final),
        ticket_presence_counts_fuzzy=dict(presence_fuzzy),
        ticket_presence_counts_llm=dict(presence_llm),
        link_totals={
            "total_links": total_links,
            "useful_links": useful_links,
            "not_useful_links": not_useful_links,
            "accessible_links": accessible_links,
            "inaccessible_links": inaccessible_links,
        },
        link_access_counts=dict(link_access_counts),
        link_usefulness_reason_counts=dict(link_usefulness_reason_counts.most_common(20)),
        attachment_extension_counts=dict(att_ext_counts),
        comparison_counts=dict(comparison_counts),
        fuzzy_vs_llm_proxy_accuracy=proxy_accuracy,
    )


def write_audit_output(
    records: list[TicketAuditRecord],
    out_path: Path,
    *,
    run_metadata: AuditRunMetadata,
) -> None:
    summary = _build_summary(records)
    output = AuditOutput(
        metadata=run_metadata,
        summary=summary,
        tickets={r.ticket_id: r for r in records},
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(output.model_dump(), fh, indent=2, ensure_ascii=False)
