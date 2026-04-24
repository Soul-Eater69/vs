"""Compare fuzzy and LLM decisions; build final recommendation."""
from __future__ import annotations

from typing import Optional

from .models import (
    AttachmentCandidate,
    ComparisonResult,
    FinalRecommendation,
    FuzzyDecision,
    LinkCandidate,
    LlmDecision,
)

_POSITIVE = {"confirmed", "likely", "possible"}
_NEGATIVE = {"absent", "inaccessible"}

_INACCESSIBLE_STATUSES = {
    "auth_required", "forbidden", "sharepoint_auth_required",
    "timeout", "connection_error", "not_found", "unknown_error",
}


def _has_explicit_evidence(
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
    statements: list[str],
) -> bool:
    return (
        bool(statements)
        or any(lnk.mentioned_as_idea_card for lnk in links)
        or any(att.mentioned_as_idea_card for att in attachments)
    )


def _all_idea_evidence_inaccessible(
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
) -> bool:
    idea_links = [lnk for lnk in links if lnk.mentioned_as_idea_card]
    if idea_links and all(lnk.access_status.value in _INACCESSIBLE_STATUSES for lnk in idea_links):
        useful_atts = [att for att in attachments if att.is_useful_fuzzy or att.fuzzy_score >= 40]
        if not useful_atts:
            return True
    return False


def _valid_candidate(
    candidate_id: Optional[str],
    attachments: list[AttachmentCandidate],
    links: list[LinkCandidate],
) -> bool:
    if not candidate_id:
        return False
    return candidate_id in {att.id for att in attachments} | {lnk.id for lnk in links}


def compare_decisions(
    fuzzy: FuzzyDecision,
    llm: LlmDecision,
) -> ComparisonResult:
    fp = fuzzy.presence
    lp = llm.presence

    if fp == lp:
        match_type = "exact_match"
    elif (fp in _POSITIVE and lp in _POSITIVE) or (fp in _NEGATIVE and lp in _NEGATIVE):
        match_type = "compatible_match"
    elif (fp in _POSITIVE and lp in _NEGATIVE) or (fp in _NEGATIVE and lp in _POSITIVE):
        match_type = "major_disagreement"
    else:
        # one or both is ambiguous — treat as compatible
        match_type = "compatible_match"

    is_major = match_type == "major_disagreement"
    is_fn_proxy = fp in _NEGATIVE and lp in _POSITIVE   # fuzzy missed it
    is_fp_proxy = fp in _POSITIVE and lp in _NEGATIVE   # fuzzy over-called it

    fid = fuzzy.primary_source_id
    lid = llm.primary_source_id
    primary_match: Optional[bool] = None
    if fid or lid:
        primary_match = fid == lid and fid is not None

    fuzzy_vs_llm = "aligned" if match_type in ("exact_match", "compatible_match") else "diverged"
    needs_review = is_major or bool(llm.warnings) or llm.confidence < 0.5

    parts: list[str] = []
    if match_type == "exact_match":
        parts.append(f"Both fuzzy and LLM agree on '{fp}'.")
    elif match_type == "compatible_match":
        parts.append(f"Fuzzy says '{fp}', LLM says '{lp}' — same group.")
    else:
        parts.append(f"Major disagreement: fuzzy='{fp}', llm='{lp}'.")

    if primary_match is True:
        parts.append(f"Both identify {fuzzy.primary_source_name!r} as primary source.")
    elif primary_match is False:
        parts.append(
            f"Primary source differs: fuzzy={fuzzy.primary_source_name!r}, "
            f"llm={llm.primary_source_name!r}."
        )

    return ComparisonResult(
        presence_match_type=match_type,
        primary_source_match=primary_match,
        fuzzy_vs_llm=fuzzy_vs_llm,
        is_major_disagreement=is_major,
        is_fuzzy_false_negative_proxy=is_fn_proxy,
        is_fuzzy_false_positive_proxy=is_fp_proxy,
        needs_manual_review=needs_review,
        comparison_reason=" ".join(parts),
    )


def build_final_recommendation(
    fuzzy: FuzzyDecision,
    llm: Optional[LlmDecision],
    comparison: Optional[ComparisonResult],
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
    statements: list[str],
) -> FinalRecommendation:
    has_statements = bool(statements)

    if llm is None:
        # No LLM — use fuzzy directly
        manual = (
            fuzzy.confidence < 0.5
            or fuzzy.presence in ("ambiguous", "possible")
        )
        return FinalRecommendation(
            presence=fuzzy.presence,
            primary_source_type=fuzzy.primary_source_type,
            primary_source_id=fuzzy.primary_source_id,
            primary_source_name=fuzzy.primary_source_name,
            extract_description=has_statements,
            extract_primary_attachment=bool(fuzzy.primary_source_type == "attachment"),
            extract_supporting_attachments=False,
            manual_review=manual,
        )

    # Major disagreement → use fuzzy, flag manual review
    if comparison and comparison.is_major_disagreement:
        return FinalRecommendation(
            presence=fuzzy.presence,
            primary_source_type=fuzzy.primary_source_type,
            primary_source_id=fuzzy.primary_source_id,
            primary_source_name=fuzzy.primary_source_name,
            extract_description=has_statements,
            extract_primary_attachment=bool(fuzzy.primary_source_type == "attachment"),
            extract_supporting_attachments=False,
            manual_review=True,
        )

    # Use LLM as the final authority
    final_presence = llm.presence

    # Guard: LLM cannot downgrade explicit evidence to absent
    if final_presence == "absent" and _has_explicit_evidence(links, attachments, statements):
        final_presence = fuzzy.presence

    # Guard: correct absent → inaccessible when all idea evidence is unreachable
    if final_presence == "absent" and _all_idea_evidence_inaccessible(links, attachments):
        final_presence = "inaccessible"

    # Validate LLM primary candidate exists in this ticket's candidates
    primary_id = llm.primary_source_id
    primary_type = llm.primary_source_type
    primary_name = llm.primary_source_name
    if primary_id and not _valid_candidate(primary_id, attachments, links):
        # Fall back to fuzzy primary if LLM hallucinated an ID
        primary_id = fuzzy.primary_source_id
        primary_type = fuzzy.primary_source_type
        primary_name = fuzzy.primary_source_name

    needs_supporting = (
        llm.needs_supporting_attachments
        and bool(llm.supporting_candidate_ids)
        and any(_valid_candidate(sid, attachments, links) for sid in llm.supporting_candidate_ids)
    )

    manual = (
        (comparison is not None and comparison.needs_manual_review)
        or llm.confidence < 0.5
        or final_presence in ("ambiguous", "possible")
    )

    return FinalRecommendation(
        presence=final_presence,
        primary_source_type=primary_type,
        primary_source_id=primary_id,
        primary_source_name=primary_name,
        extract_description=has_statements or llm.should_extract_description,
        extract_primary_attachment=bool(primary_id and primary_type in ("attachment",)),
        extract_supporting_attachments=needs_supporting,
        manual_review=manual,
    )
