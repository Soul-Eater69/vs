from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from vs_app.integrations.files.markitdown_extractor import extract_markdown
from vs_app.shared.text_cleaning import clean_extracted_text

router = APIRouter(prefix="/api", tags=["idea-cards"])

IDEA_CARDS_DIR = Path(os.environ.get("IDEA_CARDS_DIR", "idea_cards"))
SUPPORTED_EXTENSIONS = {
    ".pptx",
    ".ppt",
    ".pdf",
    ".docx",
    ".doc",
    ".txt",
    ".md",
    ".markdown",
}


@router.get("/idea-cards")
def list_idea_cards() -> dict:
    if not IDEA_CARDS_DIR.exists():
        return {"cards": []}

    cards: list[dict] = []
    for file_path in sorted(IDEA_CARDS_DIR.iterdir()):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        doc_id = file_path.stem.strip()
        if not doc_id:
            continue
        cards.append(
            {
                "doc_id": doc_id,
                "display_name": doc_id,
                "file_name": file_path.name,
                "extension": file_path.suffix[1:] if file_path.suffix else "unknown",
                "has_mapping": False,
            }
        )
    return {"cards": cards}


@router.post("/idea-cards/extract")
async def extract_idea_card(request: Request, filename: str = "idea-card") -> dict:
    clean_filename = Path(filename or "idea-card").name
    suffix = Path(clean_filename).suffix.lower()
    if suffix and suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported idea-card file type: {suffix}")

    file_bytes = await request.body()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="No file bytes provided")

    try:
        if suffix in {".txt", ".md", ".markdown"}:
            text = clean_extracted_text(file_bytes.decode("utf-8", errors="replace"))
        else:
            text = clean_extracted_text(extract_markdown(file_bytes, clean_filename))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not extract idea card: {exc}") from exc

    if not text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from this idea card")

    return {
        "filename": clean_filename,
        "text": text,
        "char_count": len(text),
    }

