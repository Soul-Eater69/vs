"""
Text consolidation from a Jira ticket.

Summary-mode source selection is intentionally opinionated:
  1. Prefer an idea-card document linked in the description.
  2. If no viable linked card exists, score Jira attachments for the best card.
  3. If no viable card exists anywhere, fall back to ticket body plus the
     strongest supporting attachments.

The output is always one consolidated string ready for LLM summarization.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ..content.formatting import (
    clean_text,
    extract_description_text,
    extract_substantive_comments,
)
from ..processing.attachment_routing import get_routing_candidates, route_attachments
from ..processing.extraction.text_cleaning import clean_extracted_text, text_looks_weak

logger = logging.getLogger(__name__)

_MAX_DESCRIPTION_CHARS = 8_000
_MAX_ATTACHMENT_CHARS = 12_000
_MAX_SUPPORTING_ATTACHMENT_CHARS = 5_000
_MAX_COMMENT_CHARS = 1_500
_MAX_TOTAL_CHARS = 24_000

_DEFAULT_MAX_SUPPORTING_ATTACHMENTS = 2
_DEFAULT_MAX_PREFETCH_ATTACHMENTS = 6
_DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE = 60_000_000
_SUPPORTED_ATTACHMENT_EXTENSIONS = {"pptx", "ppt", "pdf", "docx", "doc"}

_STRONG_IDEA_CARD_RE = re.compile(r"\bidea[\s_-]*card\b", re.IGNORECASE)
_IDEA_CARDISH_RE = re.compile(
    r"\b(idea[\s_-]*card|business[\s_-]*case|proposal|pitch|concept|initiative|deck|slides?|presentation)\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class RoutedAttachments:
    primary: Optional[dict] = None
    supporting: list[dict] = field(default_factory=list)
    prefetched: dict[str, bytes] = field(default_factory=dict)


@dataclass(slots=True)
class ResolvedSummarySource:
    origin: str = "none"  # description_link | attachment | none
    attachment: Optional[dict] = None
    text: str = ""


async def consolidate_ticket_text(
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
) -> str:
    """
    Fetch and merge all text sources for a ticket into one string.

    Source strategy:
      1. Description-linked idea card, if it can be retrieved and looks real
      2. Best attachment idea card, if one can be confirmed
      3. Ticket body + strongest supporting attachments when no card exists

    Returns a single consolidated string, trimmed to _MAX_TOTAL_CHARS.
    """
    fields: dict = ticket_data.get("fields", {}) or {}
    ticket_key = str(ticket_data.get("key") or "")
    ticket_summary = str(fields.get("summary") or ticket_key)

    description_text = clean_text(extract_description_text(fields.get("description")))
    comment_texts = extract_substantive_comments(
        comment_field=fields.get("comment") or {},
        max_comments=3,
    )

    resolved_source, attachment_routing = await _resolve_summary_source(
        ticket_data=ticket_data,
        jira_client=jira_client,
        cfg=cfg,
        ticket_summary=ticket_summary,
    )

    parts: list[str] = []
    if resolved_source.text:
        filename = str((resolved_source.attachment or {}).get("filename") or "").strip()
        source_name = "description link" if resolved_source.origin == "description_link" else "attachment"
        header_lines = [f"Source: {source_name}"]
        if filename:
            header_lines.append(f"Filename: {filename}")
        parts.append(
            "[IDEA CARD]\n"
            + "\n".join(header_lines)
            + "\n\n"
            + resolved_source.text[:_MAX_ATTACHMENT_CHARS]
        )

        if description_text:
            parts.append(f"[DESCRIPTION]\n{description_text[:_MAX_DESCRIPTION_CHARS]}")
        for idx, comment in enumerate(comment_texts, start=1):
            parts.append(f"[COMMENT {idx}]\n{comment[:_MAX_COMMENT_CHARS]}")

        logger.info(
            "Summary source for %s resolved from %s (%s)",
            ticket_key or "<unknown>",
            resolved_source.origin,
            filename or "unnamed document",
        )
    else:
        if description_text:
            parts.append(f"[DESCRIPTION]\n{description_text[:_MAX_DESCRIPTION_CHARS]}")

        parts.extend(
            await _build_supporting_attachment_parts(
                routed=attachment_routing,
                jira_client=jira_client,
                cfg=cfg,
            )
        )

        for idx, comment in enumerate(comment_texts, start=1):
            parts.append(f"[COMMENT {idx}]\n{comment[:_MAX_COMMENT_CHARS]}")

        logger.info(
            "No viable idea card resolved for %s; using ticket-body fallback",
            ticket_key or "<unknown>",
        )

    consolidated = "\n\n".join(part for part in parts if part).strip()
    return consolidated[:_MAX_TOTAL_CHARS]


async def _resolve_summary_source(
    *,
    ticket_data: dict,
    jira_client: Any,
    cfg: Any,
    ticket_summary: str,
) -> tuple[ResolvedSummarySource, RoutedAttachments]:
    attachments = list(ticket_data.get("attachments", []) or [])
    fields = ticket_data.get("fields", {}) or {}

    description_links = list(ticket_data.get("description_attachments") or [])
    if not description_links:
        description_links = [att for att in attachments if att.get("is_description_link")]

    api_attachments = list(fields.get("attachment", []) or [])
    if not api_attachments:
        api_attachments = [att for att in attachments if not att.get("is_description_link")]

    description_routing = await _route_attachment_candidates(
        attachments=description_links,
        ticket_summary=ticket_summary,
        jira_client=jira_client,
        cfg=cfg,
    )
    description_card = await _select_primary_idea_card(
        routed=description_routing,
        jira_client=jira_client,
        cfg=cfg,
        origin="description_link",
    )
    if description_card.text:
        return description_card, RoutedAttachments()

    attachment_routing = await _route_attachment_candidates(
        attachments=api_attachments,
        ticket_summary=ticket_summary,
        jira_client=jira_client,
        cfg=cfg,
    )
    attachment_card = await _select_primary_idea_card(
        routed=attachment_routing,
        jira_client=jira_client,
        cfg=cfg,
        origin="attachment",
    )
    if attachment_card.text:
        return attachment_card, attachment_routing

    return ResolvedSummarySource(), attachment_routing


async def _select_primary_idea_card(
    *,
    routed: RoutedAttachments,
    jira_client: Any,
    cfg: Any,
    origin: str,
) -> ResolvedSummarySource:
    if not routed.primary:
        return ResolvedSummarySource()

    primary_text = await _materialize_attachment_text(
        routed.primary,
        routed.prefetched,
        jira_client,
        cfg,
    )
    if not primary_text:
        return ResolvedSummarySource()

    if not _is_summary_idea_card_candidate(routed.primary, primary_text):
        return ResolvedSummarySource()

    return ResolvedSummarySource(
        origin=origin,
        attachment=routed.primary,
        text=primary_text,
    )


async def _build_supporting_attachment_parts(
    *,
    routed: RoutedAttachments,
    jira_client: Any,
    cfg: Any,
) -> list[str]:
    candidates = _unique_attachments(
        ([routed.primary] if routed.primary else []) + list(routed.supporting or [])
    )
    max_docs = int(
        getattr(cfg, "max_supplementary", _DEFAULT_MAX_SUPPORTING_ATTACHMENTS)
        or _DEFAULT_MAX_SUPPORTING_ATTACHMENTS
    )

    parts: list[str] = []
    for att in candidates:
        if len(parts) >= max_docs:
            break

        text = await _materialize_attachment_text(att, routed.prefetched, jira_client, cfg)
        if not text or text_looks_weak(text, min_words=35):
            continue

        filename = str(att.get("filename") or "attachment").strip() or "attachment"
        parts.append(
            f"[SUPPORTING ATTACHMENT: {filename}]\n{text[:_MAX_SUPPORTING_ATTACHMENT_CHARS]}"
        )

    return parts


async def _route_attachment_candidates(
    *,
    attachments: list[dict],
    ticket_summary: str,
    jira_client: Any,
    cfg: Any,
) -> RoutedAttachments:
    if not attachments:
        return RoutedAttachments()

    seed_primary, _seed_supporting, _seed_quality, seed_artifact = route_attachments(
        attachments=attachments,
        ticket_summary=ticket_summary,
        download_fn=None,
    )
    prefetch_targets = _unique_attachments(
        ([seed_primary] if seed_primary else [])
        + get_routing_candidates(seed_artifact)
        + attachments
    )
    prefetched = await _prefetch_attachment_bytes(
        jira_client=jira_client,
        attachments=prefetch_targets,
        cfg=cfg,
    )

    if prefetched:
        def cached_download(att: dict) -> bytes:
            att_id = _attachment_key(att)
            file_bytes = prefetched.get(att_id)
            if file_bytes is None:
                raise RuntimeError(f"Not prefetched: {att.get('filename')}")
            return file_bytes

        primary, supporting, _quality, _artifact = route_attachments(
            attachments=attachments,
            ticket_summary=ticket_summary,
            download_fn=cached_download,
        )
    else:
        primary, supporting, _quality, _artifact = route_attachments(
            attachments=attachments,
            ticket_summary=ticket_summary,
            download_fn=None,
        )

    return RoutedAttachments(
        primary=primary,
        supporting=list(supporting or []),
        prefetched=prefetched,
    )


async def _prefetch_attachment_bytes(
    *,
    jira_client: Any,
    attachments: list[dict],
    cfg: Any,
) -> dict[str, bytes]:
    max_prefetch = int(
        getattr(cfg, "max_prefetch_attachments", _DEFAULT_MAX_PREFETCH_ATTACHMENTS)
        or _DEFAULT_MAX_PREFETCH_ATTACHMENTS
    )
    max_size = int(
        getattr(
            cfg,
            "max_prefetch_attachment_size",
            _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE,
        )
        or _DEFAULT_MAX_PREFETCH_ATTACHMENT_SIZE
    )

    prefetched: dict[str, bytes] = {}
    for att in attachments:
        if len(prefetched) >= max_prefetch:
            break

        ext = _ext_of(att.get("filename", ""))
        size = int(att.get("size", 0) or 0)
        if ext not in _SUPPORTED_ATTACHMENT_EXTENSIONS:
            continue
        if size and size > max_size:
            continue

        att_id = _attachment_key(att)
        if not att_id or att_id in prefetched:
            continue

        try:
            file_bytes = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment prefetch skipped (%s): %s", att.get("filename"), exc)
            continue

        if file_bytes:
            prefetched[att_id] = file_bytes

    return prefetched


async def _materialize_attachment_text(
    att: dict,
    prefetched: dict[str, bytes],
    jira_client: Any,
    cfg: Any,
) -> str:
    extracted = att.get("extracted") or {}
    extracted_text = clean_extracted_text(str(extracted.get("text") or ""))
    if extracted_text:
        return extracted_text

    file_bytes = prefetched.get(_attachment_key(att))
    if file_bytes is None:
        try:
            file_bytes = await jira_client.download_attachment(att)
        except Exception as exc:
            logger.info("Attachment download skipped (%s): %s", att.get("filename"), exc)
            return ""

    return _extract_bytes_to_text(file_bytes or b"", att, cfg)


def _is_summary_idea_card_candidate(att: dict, text: str) -> bool:
    if not att or not text:
        return False

    scores = att.get("att_scores") or {}
    name = " ".join(
        str(value or "")
        for value in (
            att.get("display_text"),
            att.get("filename"),
        )
    ).strip()

    if att.get("confirmed"):
        return True
    if _STRONG_IDEA_CARD_RE.search(name) and not text_looks_weak(text, min_words=35):
        return True
    if _IDEA_CARDISH_RE.search(name):
        if float(scores.get("idea_card_likeness", 0.0) or 0.0) >= 0.45:
            return not text_looks_weak(text, min_words=45)
    if float(scores.get("idea_card_likeness", 0.0) or 0.0) >= 0.72:
        return not text_looks_weak(text, min_words=55)
    return False


def _extract_bytes_to_text(file_bytes: bytes, att: dict, cfg: Any) -> str:
    """Extract cleaned text from attachment bytes using the appropriate extractor."""
    if not file_bytes:
        return ""

    filename = (att.get("filename") or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""

    try:
        if ext in ("pptx", "ppt"):
            from processing.extraction.pptx import extract_pptx

            result = extract_pptx(
                file_bytes,
                max_slides=int(getattr(cfg, "max_slides", 60) or 60),
            )
        elif ext == "pdf":
            from processing.extraction.pdf import extract_pdf

            result = extract_pdf(
                file_bytes,
                ocr_enabled=bool(getattr(cfg, "ocr_enabled", False)),
                max_pages=int(getattr(cfg, "max_pages", 60) or 60),
            )
        elif ext in ("docx", "doc"):
            from processing.extraction.docx import extract_docx

            result = extract_docx(file_bytes)
        else:
            return ""
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", att.get("filename"), exc)
        return ""

    chunks = result.get("chunks", []) or []
    text = "\n".join(
        str(chunk.get("text") or "")
        for chunk in chunks
        if chunk.get("text") and not chunk.get("is_boilerplate")
    )
    return clean_extracted_text(text)


def _unique_attachments(attachments: list[Optional[dict]]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for att in attachments:
        if not att:
            continue
        att_id = _attachment_key(att)
        if not att_id or att_id in seen:
            continue
        seen.add(att_id)
        out.append(att)
    return out


def _attachment_key(att: dict) -> str:
    return str(att.get("id") or att.get("attachment_id") or att.get("filename") or "")


def _ext_of(filename: str) -> str:
    name = (filename or "").lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""
