"""LLM-based idea-card classifier — receives clean metadata, not fuzzy scores."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from vs_app.integrations.clients.llm import complete_text
from vs_app.modules.prompts.loader import load_prompt_yaml, safe_json_extract

from .models import AttachmentCandidate, FuzzyDecision, LinkCandidate, LlmDecision

logger = logging.getLogger(__name__)

_MAX_DESC_CHARS = 600


def _should_call_llm(fuzzy: FuzzyDecision, attachments: list[AttachmentCandidate]) -> bool:
    """Heuristic: call LLM for uncertain cases when llm_every_ticket=False."""
    if fuzzy.presence == "confirmed" and fuzzy.confidence >= 0.9 and not fuzzy.needs_llm_review:
        return False
    if fuzzy.presence == "absent" and fuzzy.confidence >= 0.85 and not fuzzy.needs_llm_review:
        strong_atts = [a for a in attachments if a.fuzzy_score >= 30]
        if not strong_atts:
            return False
    return True


def _build_clean_llm_input(
    ticket_id: str,
    title: str,
    description: str,
    statements: list[str],
    value_streams: list[str],
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
) -> str:
    """
    Build metadata-only LLM input. Excludes fuzzy scores, roles, reasons, and decisions.
    LLM must assess independently from raw metadata.
    """
    desc = description[:_MAX_DESC_CHARS]
    if len(description) > _MAX_DESC_CHARS:
        desc += "... [truncated]"

    payload: dict[str, Any] = {
        "ticket_id": ticket_id,
        "title": title,
        "description_excerpt": desc,
        "description_idea_card_statements": statements,
        "value_streams": value_streams,
        "links": [
            {
                "id": lnk.id,
                "url_host": lnk.url_host,
                "url_path_hint": lnk.url_path_hint,
                "raw_context": lnk.raw_context[:150],
                "mentioned_as_idea_card": lnk.mentioned_as_idea_card,
                "access_status": lnk.access_status.value,
                "http_status": lnk.http_status,
                "content_type": lnk.content_type,
                "access_reason": lnk.not_accessible_reason,
            }
            for lnk in links
        ],
        "attachments": [
            {
                "id": att.id,
                "filename": att.filename,
                "extension": att.extension,
                "size": att.size,
                "mentioned_as_idea_card": att.mentioned_as_idea_card,
            }
            for att in attachments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def classify_with_llm(
    ticket_id: str,
    title: str,
    description: str,
    statements: list[str],
    value_streams: list[str],
    links: list[LinkCandidate],
    attachments: list[AttachmentCandidate],
    llm_client: Any,
    *,
    fuzzy: Optional[FuzzyDecision] = None,
    llm_every_ticket: bool = True,
) -> Optional[LlmDecision]:
    """
    Classify idea-card presence using raw ticket metadata only.

    Side effects: sets is_useful_llm and usefulness_reason_llm on each candidate.
    Returns LlmDecision or None if skipped/failed.

    The LLM receives no fuzzy scores, roles, or decisions — it works independently.
    """
    if not llm_every_ticket and fuzzy is not None:
        if not _should_call_llm(fuzzy, attachments):
            return None

    try:
        prompt_data = load_prompt_yaml("idea_card_audit")
    except Exception as exc:
        logger.warning("[%s] Could not load idea_card_audit.yaml: %s", ticket_id, exc)
        return None

    system_prompt = str(prompt_data.get("system", "")).strip()
    user_template = str(prompt_data.get("user_template", "")).strip()

    ticket_json = _build_clean_llm_input(
        ticket_id, title, description, statements, value_streams, links, attachments
    )
    # Use direct substitution — ticket_json contains raw JSON with curly braces,
    # which would break render_prompt's trailing .format(**kwargs) call.
    user_prompt = re.sub(r"\{\{\s*ticket_json\s*\}\}", ticket_json, user_template)

    try:
        raw = complete_text(
            prompt=user_prompt,
            llm_client=llm_client,
            system_prompt=system_prompt,
            max_output_tokens=1200,
            temperature=0.1,
        )
        parsed = safe_json_extract(raw)
    except Exception as exc:
        logger.warning("[%s] LLM call failed: %s", ticket_id, exc)
        raise

    # Apply per-link assessments back to candidates
    link_map = {lnk.id: lnk for lnk in links}
    att_map = {att.id: att for att in attachments}

    useful_link_ids: list[str] = []
    not_useful_link_ids: list[str] = []

    for assessment in parsed.get("link_assessments") or []:
        aid = str(assessment.get("id") or "")
        if aid not in link_map:
            continue
        is_useful = bool(assessment.get("is_useful"))
        reason = str(assessment.get("reason") or "")
        link_map[aid].is_useful_llm = is_useful
        link_map[aid].usefulness_reason_llm = reason
        if is_useful:
            useful_link_ids.append(aid)
        else:
            not_useful_link_ids.append(aid)

    for assessment in parsed.get("attachment_assessments") or []:
        aid = str(assessment.get("id") or "")
        if aid not in att_map:
            continue
        att_map[aid].is_useful_llm = bool(assessment.get("is_useful"))
        att_map[aid].usefulness_reason_llm = str(assessment.get("reason") or "")

    return LlmDecision(
        presence=str(parsed.get("presence") or "absent"),
        confidence=float(parsed.get("confidence") or 0.0),
        primary_source_type=parsed.get("primary_candidate_kind") or None,
        primary_source_id=parsed.get("primary_candidate_id") or None,
        primary_source_name=parsed.get("primary_candidate_name") or None,
        useful_link_ids=useful_link_ids,
        not_useful_link_ids=not_useful_link_ids,
        needs_supporting_attachments=bool(parsed.get("needs_supporting_attachments")),
        supporting_candidate_ids=list(parsed.get("supporting_candidate_ids") or []),
        should_extract_description=bool(parsed.get("should_extract_description", True)),
        reasoning=str(parsed.get("reasoning") or ""),
        warnings=list(parsed.get("warnings") or []),
    )
