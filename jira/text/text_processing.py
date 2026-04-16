from __future__ import annotations

import re
from typing import Any, Optional

JUNK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^please see attach", re.IGNORECASE),
    re.compile(r"^see attached", re.IGNORECASE),
    re.compile(r"^tbd$", re.IGNORECASE),
    re.compile(r"^todo$", re.IGNORECASE),
    re.compile(r"^n/a$", re.IGNORECASE),
    re.compile(r"^none$", re.IGNORECASE),
    re.compile(r"^\[insert.+\]", re.IGNORECASE),
    re.compile(r"^as discussed", re.IGNORECASE),
    re.compile(r"^see confluence", re.IGNORECASE),
    re.compile(r"^details in the deck", re.IGNORECASE),
    re.compile(r"^refer to the attached", re.IGNORECASE),
    re.compile(r"^attached", re.IGNORECASE),
]

_JIRA_MARKUP_RE = re.compile(
    r"\{code.*?\}.*?\{code\}|"
    r"\{panel.*?\}.*?\{panel\}|"
    r"\{noformat.*?\}.*?\{noformat\}|"
    r"\{[a-z]+[^\}]*\}",
    re.DOTALL | re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^h[1-5]\.\s*", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|(?:.*?\|)+\s*$", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"[*_]{1,2}(.*?)[*_]{1,2}")
_COMMENT_CHATTER_RE = re.compile(
    r"^(?:\[~[a-zA-Z0-9]+\]\s*)?"
    r"(?:(?:moved|transitioned|changed status|updated|assigned|resolved|closed|reopened)\s*|"
    r"(?:done|ok|ack|acknowledged|noted|thanks|cheers|\+1|-1|approved|lgtm)\s*$|"
    r"(?:see attached|see comments|see above|as per|per above|as discussed)\b|"
    r"(?:sent|forwarded|cc|fyi)\b)",
    re.IGNORECASE,
)
_BOT_AUTHOR_MARKERS = ("atlassian-bot", "jira-bot", "automation", "webhook")


def extract_adf_text(adf_node: Any) -> str:
    """Extract readable text from Atlassian Document Format with basic structure."""
    text = _extract_adf_node(adf_node)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_adf_html(adf: Any) -> str:
    """Backward-compatible alias for older imports/tests."""
    return extract_adf_text(adf)


def clean_jira_markup(text: str) -> str:
    """Strip Jira wiki markup, leaving plain readable text."""
    if not text:
        return ""
    text = _JIRA_MARKUP_RE.sub(" ", text)
    text = _HEADING_RE.sub("", text)
    text = _TABLE_ROW_RE.sub(" ", text)
    text = _BOLD_ITALIC_RE.sub(r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_description(raw_description: Any, *, min_words: int = 10) -> str:
    """Clean a Jira description into plain text, returning empty string for junk/empty values."""
    if isinstance(raw_description, (dict, list)):
        text = extract_adf_text(raw_description)
    else:
        text = str(raw_description or "")

    text = clean_jira_markup(text)
    if not text:
        return ""
    if len(text.split()) < min_words:
        return ""

    text_lower = text.lower().strip()
    if any(pattern.match(text_lower) for pattern in JUNK_PATTERNS):
        return ""
    return text


def classify_description(
    raw_text: Any,
    *,
    junk_max_words: int = 10,
    thin_max_words: int = 50,
    rich_min_words: int = 150,
) -> tuple[str, Optional[dict[str, Any]]]:
    """Classify Jira description text into empty | junk | thin | usable | rich."""
    if raw_text is None:
        return "empty", None

    if isinstance(raw_text, (dict, list)):
        text = extract_adf_text(raw_text)
    else:
        text = str(raw_text or "")

    if not text.strip():
        return "empty", None

    text = clean_jira_markup(text)
    word_count = len(text.split())
    text_lower = text.lower().strip()

    if word_count < junk_max_words:
        return "junk", None

    if any(pattern.match(text_lower) for pattern in JUNK_PATTERNS):
        return "junk", None

    if word_count < thin_max_words:
        return "thin", {"text": text, "needs_enrichment": True, "word_count": word_count}

    if word_count < rich_min_words:
        return "usable", {"text": text, "chunk_strategy": "single", "word_count": word_count}

    return "rich", {"text": text, "chunk_strategy": "split_paragraphs", "word_count": word_count}


def is_bot_author(author: str) -> bool:
    return any(marker in (author or "").lower() for marker in _BOT_AUTHOR_MARKERS)


def is_comment_chatter(text: str, *, min_words: int = 10) -> bool:
    cleaned = clean_jira_markup(text)
    if len(cleaned.split()) < min_words:
        return True
    return bool(_COMMENT_CHATTER_RE.match(cleaned))


def extract_comment_texts(
    comment_container: dict[str, Any],
    *,
    min_words: int = 15,
    max_comments: int = 5,
    skip_bots: bool = True,
    skip_chatter: bool = True,
) -> list[str]:
    """Extract cleaned substantive comment texts from a Jira comment field."""
    comments = (comment_container or {}).get("comments", []) or []
    texts: list[str] = []

    for comment in comments:
        author_obj = comment.get("author") or {}
        author = str(author_obj.get("displayName") or author_obj.get("name") or "")
        if skip_bots and is_bot_author(author):
            continue

        body = comment.get("body")
        if isinstance(body, (dict, list)):
            text = extract_adf_text(body)
        else:
            text = str(body or "")

        cleaned = clean_jira_markup(text).strip()
        if not cleaned or len(cleaned.split()) < min_words:
            continue
        if skip_chatter and is_comment_chatter(cleaned):
            continue

        texts.append(cleaned)
        if len(texts) >= max_comments:
            break

    return texts


def _extract_adf_node(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_node(item) for item in node)
    if not isinstance(node, dict):
        return str(node)

    node_type = str(node.get("type") or "")
    content = node.get("content") or []

    if node_type == "text":
        return str(node.get("text") or "")
    if node_type == "hardBreak":
        return "\n"
    if node_type in ("doc", "tableCell", "tableHeader"):
        return "".join(_extract_adf_node(child) for child in content)
    if node_type in ("paragraph", "heading", "blockquote", "listItem"):
        text = "".join(_extract_adf_node(child) for child in content).strip()
        return f"{text}\n" if text else ""
    if node_type in ("bulletList", "orderedList"):
        items = [
            _extract_adf_node(child).strip()
            for child in content
            if str(child.get("type") or "") == "listItem"
        ]
        items = [item for item in items if item]
        return "\n".join(f"- {item}" for item in items) + "\n" if items else ""
    if node_type == "table":
        table_text = _extract_adf_table(node)
        return f"\n{table_text}\n" if table_text else ""
    if node_type in ("mediaSingle", "mediaGroup"):
        image_label = _extract_media_label(node)
        return f"[Image: {image_label}]"

    return "".join(_extract_adf_node(child) for child in content)


def _extract_adf_table(table_node: dict[str, Any]) -> str:
    rows: list[list[str]] = []
    for row in table_node.get("content") or []:
        if str(row.get("type") or "") != "tableRow":
            continue
        cells: list[str] = []
        for cell in row.get("content") or []:
            if str(cell.get("type") or "") not in ("tableCell", "tableHeader"):
                continue
            cell_text = " ".join(_extract_adf_node(cell).split())
            cells.append(cell_text)
        if any(cells):
            rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(row) for row in rows)
    normalized = [row + [""] * (col_count - len(row)) for row in rows]
    header = normalized[0]
    lines = [
        " | ".join(header) + " |",
        " | ".join(["---"] * col_count) + " |",
    ]
    for row in normalized[1:]:
        lines.append(" | ".join(row) + " |")
    return "\n".join(lines)


def _extract_media_label(node: dict[str, Any]) -> str:
    attrs = node.get("attrs") or {}
    label = attrs.get("alt") or attrs.get("title") or attrs.get("id") or attrs.get("type")
    if label:
        return str(label)
    for child in node.get("content") or []:
        if isinstance(child, dict):
            child_label = _extract_media_label(child)
            if child_label and child_label != "unknown":
                return child_label
    return "unknown"


__all__ = [
    "classify_description",
    "clean_description",
    "clean_jira_markup",
    "extract_adf_html",
    "extract_adf_text",
    "extract_comment_texts",
    "is_bot_author",
    "is_comment_chatter",
]
