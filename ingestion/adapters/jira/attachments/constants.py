"""Attachment-specific constants and parsing patterns for Jira attachments."""

from __future__ import annotations

import re

ATTACHMENT_FETCH_RETRIES = 4
ATTACHMENT_CONVERT_TIMEOUT_SEC = 90.0
ATTACHMENT_FETCH_BASE_DELAY_SEC = 1.0

DESC_WIKI_LINK_RE = re.compile(r"\[(?P<label>[^|]+)\|(?P<url>https?://[^\]]+)\]")
DESC_MD_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)")
DESC_BARE_FILE_URL_RE = re.compile(
    r"(?P<url>https?://[^\s<>\"']+\.(?P<ext>pptx|ppt|pdf|docx|doc|xlsx|xls|csv|png|jpg|jpeg|gif|bmp|tiff|webp)(?:\?[^\s<>\"']*)?)",
    re.IGNORECASE,
)

EXT_TO_MIME: dict[str, str] = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}
