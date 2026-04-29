from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request


router = APIRouter(tags=["idea-cards"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_IDEA_CARDS_DIR = _REPO_ROOT / "idea_cards"
_MAPPINGS_PATH = _REPO_ROOT / "mappings.json"

from vs_app.shared.constants import CANONICAL_VALUE_STREAMS


@router.get("/api/canonical-value-streams")
async def get_canonical_value_streams() -> dict:
    by_category: dict[str, list] = {}
    for vs in CANONICAL_VALUE_STREAMS:
        cat = vs.get("category", "uncategorized")
        by_category.setdefault(cat, []).append(
            {
                "id": vs.get("id"),
                "name": vs.get("name"),
                "category": cat,
            }
        )
    return {
        "total": len(CANONICAL_VALUE_STREAMS),
        "by_category": by_category,
        "all": CANONICAL_VALUE_STREAMS,
    }


@router.get("/api/idea-cards")
async def list_idea_cards() -> dict:
    if not _IDEA_CARDS_DIR.exists():
        return {"cards": []}

    mappings: dict[str, dict] = {}
    if _MAPPINGS_PATH.exists():
        with _MAPPINGS_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        mappings = {
            entry["doc_id"]: entry
            for entry in data.get("idea_cards", [])
            if "doc_id" in entry
        }

    cards = []
    for file_path in sorted(_IDEA_CARDS_DIR.iterdir()):
        if not file_path.is_file():
            continue
        doc_id = file_path.stem
        if not doc_id:
            continue
        entry = mappings.get(doc_id, {})
        cards.append(
            {
                "doc_id": doc_id,
                "display_name": entry.get("title", doc_id),
                "file_name": file_path.name,
                "extension": file_path.suffix[1:] if file_path.suffix else "unknown",
                "has_mapping": bool(entry),
            }
        )
    return {"cards": cards}


@router.post("/api/idea-cards/extract")
async def extract_uploaded_idea_card(
    request: Request,
    filename: str = Query(..., min_length=1),
) -> dict:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded idea card is empty")

    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pptx", ".ppt", ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported idea-card file type. Use PPTX, PDF, DOCX, TXT, or Markdown.",
        )

    try:
        if suffix in {".txt", ".md", ".markdown"}:
            raw_text = data.decode("utf-8", errors="ignore")
        else:
            from vs_app.integrations.files.markitdown_extractor import extract_markdown

            raw_text = extract_markdown(data, safe_name)

        from vs_app.shared.text_cleaning import clean_extracted_text

        text = clean_extracted_text(raw_text)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not extract idea card text: {exc}") from exc

    if not text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted from this idea card")

    return {
        "filename": safe_name,
        "text": text,
        "char_count": len(text),
    }


@router.get("/api/mappings")
async def get_mappings() -> dict:
    if not _MAPPINGS_PATH.exists():
        raise HTTPException(status_code=404, detail="mappings.json not found")
    with _MAPPINGS_PATH.open(encoding="utf-8") as f:
        return json.load(f)
