"""Text extraction from Jira ticket fields, comments, and attachments."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ADF (Atlassian Document Format) → plain text
# ---------------------------------------------------------------------------

def adf_to_text(node: Any) -> str:
    """Extract readable text from ADF JSON."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(item) for item in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = node.get("type") or ""
    content = node.get("content") or []

    if node_type == "text":
        return node.get("text") or ""
    if node_type == "hardBreak":
        return "\n"
    if node_type in ("doc", "tableCell", "tableHeader"):
        return "".join(adf_to_text(child) for child in content)
    if node_type in ("paragraph", "heading", "blockquote", "listItem"):
        text = "".join(adf_to_text(child) for child in content).strip()
        return f"{text}\n" if text else ""
    if node_type in ("bulletList", "orderedList"):
        items = [adf_to_text(child).strip() for child in content]
        return "\n".join(f"- {item}" for item in items if item) + "\n"
    if node_type == "table":
        rows = []
        for child in content:
            if child.get("type") == "tableRow":
                cells = [adf_to_text(cell).strip() for cell in child.get("content", [])]
                rows.append(" | ".join(cells))
        return "\n".join(rows) + "\n" if rows else ""
    if node_type == "mediaInline":
        return f"[attachment: {node.get('attrs', {}).get('title', 'file')}]"

    return "".join(adf_to_text(child) for child in content)


# ---------------------------------------------------------------------------
# Description extraction
# ---------------------------------------------------------------------------

def extract_description(fields: dict) -> str:
    """Get plain-text description from Jira fields."""
    raw = fields.get("description")
    if isinstance(raw, (dict, list)):
        text = adf_to_text(raw)
    else:
        text = raw or ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# Comment extraction
# ---------------------------------------------------------------------------

_CHATTER_RE = re.compile(
    r"^(?:\[~[a-zA-Z0-9]+\]\s*)?"
    r"(?:(?:moved|transitioned|changed status|updated|assigned|resolved|closed|reopened)\s*|"
    r"(?:done|ok|ack|acknowledged|noted|thanks|cheers|\+1|-1|approved|lgtm)\s*$|"
    r"(?:see attached|see comments|see above|as per|per above|as discussed)\b|"
    r"(?:sent|forwarded|cc|fyi)\b)",
    re.IGNORECASE,
)


def extract_comments(fields: dict, max_comments: int = 3) -> list[str]:
    """Extract substantive comment texts, skipping bot chatter."""
    comment_data = fields.get("comment") or {}
    raw_comments = comment_data.get("comments", [])

    result: list[str] = []
    for c in raw_comments:
        body = c.get("body", "")
        if isinstance(body, (dict, list)):
            body = adf_to_text(body)
        body = (body or "").strip()

        # Skip short or chattery comments
        if len(body.split()) < 10:
            continue
        if _CHATTER_RE.match(body):
            continue

        result.append(body)
        if len(result) >= max_comments:
            break

    return result


# ---------------------------------------------------------------------------
# Metadata extraction (flat, retrieval-safe)
# ---------------------------------------------------------------------------

def extract_metadata(fields: dict, ticket_key: str) -> dict:
    """Extract core metadata from Jira fields. Flat dict, no config-driven custom fields."""
    reporter_obj = fields.get("reporter") or {}
    return {
        "ticket_key": ticket_key,
        "title": fields.get("summary", ""),
        "summary": fields.get("summary", ""),
        "reporter": reporter_obj.get("displayName", reporter_obj.get("name", "")),
        "created": fields.get("created", ""),
        "updated": fields.get("updated", ""),
        "labels": [lbl for lbl in (fields.get("labels") or []) if isinstance(lbl, str)],
        "components": [
            c.get("name", "") for c in (fields.get("components") or []) if isinstance(c, dict)
        ],
        "issue_type": (fields.get("issuetype") or {}).get("name", ""),
        "status": (fields.get("status") or {}).get("name", ""),
        "priority": (fields.get("priority") or {}).get("name", ""),
        "resolution": (fields.get("resolution") or {}).get("name", ""),
    }


# ---------------------------------------------------------------------------
# Attachment text extraction
# ---------------------------------------------------------------------------

EXTRACTABLE_EXTS = {"pptx", "ppt", "pdf", "docx", "doc", "xlsx", "xls", "csv"}


def extract_attachment_texts(
    attachments: list[dict],
    download_fn: Callable[[dict], bytes],
    max_attachments: int = 5,
) -> list[dict]:
    """
    Download and extract text from viable attachments.

    Returns list of {"attachment_id", "filename", "text", "ext"}.
    """
    results: list[dict] = []

    for att in attachments:
        if len(results) >= max_attachments:
            break

        filename = (att.get("filename") or "").strip()
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in EXTRACTABLE_EXTS:
            continue

        att_id = str(att.get("id") or filename)
        try:
            file_bytes = download_fn(att)
        except Exception as exc:
            logger.warning("Download failed for %s: %s", filename, exc)
            continue

        text = _extract_text(file_bytes, ext, filename=filename)
        if text:
            results.append({
                "attachment_id": att_id,
                "filename": filename,
                "text": text,
                "ext": ext,
            })

    return results


def _extract_text(file_bytes: bytes, ext: str, filename: str = "") -> str:
    """Extract markdown text from file bytes using MarkItDown."""
    effective_name = filename or f"document.{ext}"
    try:
        if ext in ("pptx", "ppt", "pdf", "docx", "doc"):
            return _extract_markdown(file_bytes, effective_name)
        elif ext in ("xlsx", "xls"):
            return _extract_xlsx(file_bytes)
        elif ext == "csv":
            return file_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", effective_name, exc)
    return ""


def _extract_markdown(data: bytes, filename: str) -> str:
    """Extract markdown from any supported file type using MarkItDown."""
    from processing.extraction.markitdown import extract_markdown
    return extract_markdown(data, filename)


def _extract_xlsx(data: bytes) -> str:
    import io
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Markdown-aware chunking (like BaseDocumentChunker, but without delia/Azure)
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _split_by_headers(markdown: str) -> list[dict]:
    """
    Split markdown into sections by header boundaries.

    Returns list of {"header": str, "level": int, "body": str}.
    If no headers found, returns the whole text as a single section.
    """
    matches = list(_HEADER_RE.finditer(markdown))
    if not matches:
        return [{"header": "", "level": 0, "body": markdown.strip()}] if markdown.strip() else []

    sections: list[dict] = []

    # Text before first header
    preamble = markdown[:matches[0].start()].strip()
    if preamble:
        sections.append({"header": "", "level": 0, "body": preamble})

    for i, match in enumerate(matches):
        level = len(match.group(1))
        header = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        if body or header:
            sections.append({"header": header, "level": level, "body": body})

    return sections


def _build_header_hierarchy(sections: list[dict]) -> list[dict]:
    """Add header_hierarchy (e.g. 'Policy > Scope > Details') to each section."""
    stack: list[str] = []  # (level, header) pairs tracked as stack
    stack_levels: list[int] = []
    result = []

    for sec in sections:
        level = sec["level"]
        header = sec["header"]

        if level > 0 and header:
            # Pop stack back to parent level
            while stack_levels and stack_levels[-1] >= level:
                stack.pop()
                stack_levels.pop()
            stack.append(header)
            stack_levels.append(level)

        result.append({
            **sec,
            "header_hierarchy": " > ".join(stack) if stack else header,
        })

    return result


def _split_oversized(text: str, max_words: int, overlap_words: int = 50) -> list[str]:
    """Split text that exceeds max_words into overlapping word-boundary chunks."""
    words = text.split()
    if len(words) <= max_words:
        return [text] if text.strip() else []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap_words
    return chunks


def _merge_small_sections(
    sections: list[dict], min_words: int = 30
) -> list[dict]:
    """Merge sections that are too small into their neighbor."""
    if len(sections) <= 1:
        return sections

    merged: list[dict] = [sections[0]]
    for sec in sections[1:]:
        prev = merged[-1]
        if len(prev["body"].split()) < min_words:
            # Merge into previous
            combined_header = prev["header_hierarchy"] or sec["header_hierarchy"]
            prev["body"] = (prev["body"] + "\n\n" + sec["body"]).strip()
            prev["header_hierarchy"] = combined_header
            if sec["header"]:
                prev["header"] = prev["header"] or sec["header"]
        else:
            merged.append(sec)

    # Check last chunk too
    if len(merged) > 1 and len(merged[-1]["body"].split()) < min_words:
        last = merged.pop()
        merged[-1]["body"] = (merged[-1]["body"] + "\n\n" + last["body"]).strip()

    return merged


def chunk_markdown(
    markdown: str, max_words: int = 500, overlap_words: int = 50, min_words: int = 30,
) -> list[dict]:
    """
    Header-aware markdown chunking.

    1. Split by markdown headers (respects document structure)
    2. Build header hierarchy (tracks parent context)
    3. Merge small sections (avoids tiny chunks)
    4. Split oversized sections (with word overlap)

    Returns list of {"text", "header", "header_hierarchy", "word_count"}.
    """
    sections = _split_by_headers(markdown)
    sections = _build_header_hierarchy(sections)
    sections = _merge_small_sections(sections, min_words=min_words)

    chunks: list[dict] = []
    for sec in sections:
        # Prepend header to body for context
        header_prefix = f"## {sec['header']}\n\n" if sec["header"] else ""
        full_text = header_prefix + sec["body"]

        parts = _split_oversized(full_text, max_words=max_words, overlap_words=overlap_words)
        for part in parts:
            chunks.append({
                "text": part,
                "header": sec["header"],
                "header_hierarchy": sec.get("header_hierarchy", ""),
                "word_count": len(part.split()),
            })

    return chunks


def build_chunks(
    ticket_key: str,
    description: str,
    comments: list[str],
    attachment_texts: list[dict],
    max_chunk_words: int = 500,
) -> list[dict]:
    """
    Build chunk records from extracted text sources.

    Uses header-aware splitting for description and attachments (markdown),
    one chunk per comment.
    """
    chunks: list[dict] = []
    idx = 0

    # Description chunks (header-aware)
    if description.strip():
        for mc in chunk_markdown(description, max_words=max_chunk_words):
            chunks.append(_make_chunk(
                idx=idx, source="description", text=mc["text"],
                section_title=mc.get("header") or "Description",
                header_hierarchy=mc.get("header_hierarchy", ""),
            ))
            idx += 1

    # Comment chunks (one chunk per comment, truncated)
    for i, comment in enumerate(comments):
        truncated = " ".join(comment.split()[:max_chunk_words])
        chunks.append(_make_chunk(
            idx=idx, source="comment", text=truncated,
            section_title=f"Comment {i + 1}",
        ))
        idx += 1

    # Attachment chunks (header-aware markdown splitting)
    for att in attachment_texts:
        for mc in chunk_markdown(att["text"], max_words=max_chunk_words):
            chunks.append(_make_chunk(
                idx=idx, source=f"attachment:{att['ext']}",
                text=mc["text"],
                section_title=mc.get("header") or att["filename"],
                header_hierarchy=mc.get("header_hierarchy", ""),
                attachment_id=att["attachment_id"],
                attachment_name=att["filename"],
            ))
            idx += 1

    # Assign stable chunk UIDs
    for chunk in chunks:
        uid_input = f"{ticket_key}:{chunk['chunk_id']}:{chunk['text'][:200]}"
        chunk["chunk_uid"] = hashlib.sha256(uid_input.encode()).hexdigest()[:16]

    return chunks


def _make_chunk(
    idx: int,
    source: str,
    text: str,
    section_title: str = "",
    header_hierarchy: str = "",
    attachment_id: str = "",
    attachment_name: str = "",
) -> dict:
    return {
        "chunk_id": f"{source}-{idx}",
        "chunk_index": idx,
        "source": source,
        "text": text,
        "word_count": len(text.split()),
        "section_title": section_title,
        "header_hierarchy": header_hierarchy,
        "attachment_id": attachment_id,
        "attachment_name": attachment_name,
    }
