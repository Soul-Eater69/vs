from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from .markitdown_extractor import extract_markdown
from vs_app.modules.value_streams.canonical import canonicalize_foundational_mentions
from vs_app.shared.text_cleaning import clean_extracted_text


FOUNDATIONAL_SECTION_HEADERS = (
    "foundational value stream",
    "foundational value streams",
    "capability value stream",
    "capability value streams",
    "operational value stream",
    "operational value streams",
    "operational value stream examples",
    "value stream examples",
)


def resolve_idea_card_path(
    input_path: str | Path | None = None,
    *,
    doc_id: str | None = None,
    local_card_dir: str | Path = "local_cards",
) -> Path:
    """
    Resolve an idea-card file either from an explicit path or by doc_id lookup.

    The doc_id lookup preserves the older workflow where we searched a local
    idea-cards directory for ``{doc_id}.*`` regardless of extension.
    """
    if input_path is not None:
        path = Path(input_path)
    elif doc_id:
        base_dir = Path(local_card_dir)
        matches = sorted(base_dir.glob(f"{doc_id}.*"))
        if not matches:
            raise FileNotFoundError(
                f"Idea card not found for doc_id '{doc_id}' in {base_dir}"
            )
        path = matches[0]
    else:
        raise ValueError("Provide either input_path or doc_id to resolve an idea card")

    if not path.exists():
        raise FileNotFoundError(f"Idea card file not found: {path}")
    return path


def extract_idea_card_text(
    input_path: str | Path | None = None,
    *,
    doc_id: str | None = None,
    local_card_dir: str | Path = "local_cards",
    max_chars: Optional[int] = None,
) -> str:
    """
    Extract text from a local idea-card file.

    We intentionally do not truncate by default because the downstream pipeline
    performs its own condensation/summarization. ``max_chars`` is only a hard
    safety cap for callers that explicitly want one.
    """
    path = resolve_idea_card_path(
        input_path,
        doc_id=doc_id,
        local_card_dir=local_card_dir,
    )

    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown"}:
        raw_text = path.read_text(encoding="utf-8")
    else:
        raw_text = extract_markdown(path.read_bytes(), path.name)

    text = clean_extracted_text(raw_text)
    if not text.strip():
        raise ValueError(f"No text could be extracted from {path}")

    if max_chars is not None and max_chars > 0:
        return text[:max_chars]
    return text


def extract_candidate_value_stream_mentions(text: str) -> list[str]:
    """Extract likely value-stream mentions from idea-card text."""
    lines = [line.strip(" -\u2022\t") for line in (text or "").splitlines() if line.strip()]

    mentions: list[str] = []
    capture_next = False

    for line in lines:
        lower = line.lower()

        if any(header in lower for header in FOUNDATIONAL_SECTION_HEADERS):
            capture_next = True
            if ":" in line:
                mentions.extend(split_possible_vs_values(line.split(":", 1)[1]))
            continue

        if not capture_next:
            continue

        if len(line.split()) > 18 and not any(sep in line for sep in [",", ";", "|"]):
            capture_next = False
            continue

        mentions.extend(split_possible_vs_values(line))

    return dedupe_preserve_order(mentions)


def split_possible_vs_values(value: str) -> list[str]:
    parts = re.split(r"[,;|/]+", value or "")
    cleaned: list[str] = []
    for part in parts:
        item = " ".join(part.strip(" -\u2022\t").split())
        if len(item) < 3:
            continue
        cleaned.append(item)
    return cleaned


def dedupe_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def build_foundational_metadata(text: str) -> dict:
    raw_mentions = extract_candidate_value_stream_mentions(text)
    canonical = canonicalize_foundational_mentions(raw_mentions)

    return {
        "foundational_value_streams_raw": dedupe_preserve_order([item.raw for item in canonical]),
        "foundational_value_streams_canonical": dedupe_preserve_order(
            [item.canonical_name for item in canonical]
        ),
        "foundational_value_stream_entity_ids": dedupe_preserve_order([
            item.entity_id for item in canonical if item.entity_id
        ]),
        "foundational_value_stream_matches": [
            {
                "raw": item.raw,
                "canonical_name": item.canonical_name,
                "entity_id": item.entity_id,
                "match_type": item.match_type,
            }
            for item in canonical
        ],
    }
