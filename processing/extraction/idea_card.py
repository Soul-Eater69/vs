from __future__ import annotations

from pathlib import Path
from typing import Optional

from .markitdown import extract_markdown
from .text_cleaning import clean_extracted_text


def resolve_idea_card_path(
    input_path: str | Path | None = None,
    *,
    doc_id: str | None = None,
    idea_cards_dir: str | Path = "idea_cards",
) -> Path:
    """
    Resolve an idea-card file either from an explicit path or by doc_id lookup.

    The doc_id lookup preserves the older workflow where we searched a local
    idea-cards directory for ``{doc_id}.*`` regardless of extension.
    """
    if input_path is not None:
        path = Path(input_path)
    elif doc_id:
        base_dir = Path(idea_cards_dir)
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
    idea_cards_dir: str | Path = "idea_cards",
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
        idea_cards_dir=idea_cards_dir,
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
