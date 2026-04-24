from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from core import clean_ppt_text
from core.constants import CANONICAL_VALUE_STREAMS
from src.services.value_stream.value_stream_service import ValueStreamService

from api.config import IDEA_CARDS_DIR, MAPPINGS_PATH

router = APIRouter()


@router.get("/api/canonical-value-streams")
def get_canonical_value_streams() -> dict:
    by_category: dict[str, list] = {}
    for vs in CANONICAL_VALUE_STREAMS:
        cat = vs.get("category", "uncategorized")
        by_category.setdefault(cat, []).append({
            "id": vs.get("id"),
            "name": vs.get("name"),
            "category": cat,
        })
    return {"total": len(CANONICAL_VALUE_STREAMS), "by_category": by_category, "all": CANONICAL_VALUE_STREAMS}


@router.get("/api/idea-cards")
def list_idea_cards() -> dict:
    if not IDEA_CARDS_DIR.exists():
        return {"cards": []}

    mappings: dict = {}
    if MAPPINGS_PATH.exists():
        with open(MAPPINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        mappings = {entry["doc_id"]: entry for entry in data.get("idea_cards", []) if "doc_id" in entry}

    cards = []
    for file_path in sorted(IDEA_CARDS_DIR.iterdir()):
        if not file_path.is_file():
            continue
        doc_id = file_path.stem
        if not doc_id:
            continue
        entry = mappings.get(doc_id, {})
        cards.append({
            "doc_id": doc_id,
            "display_name": entry.get("title", doc_id),
            "file_name": file_path.name,
            "extension": file_path.suffix[1:] if file_path.suffix else "unknown",
            "has_mapping": bool(entry),
        })
    return {"cards": cards}


@router.get("/api/mappings")
def get_mappings() -> dict:
    if not MAPPINGS_PATH.exists():
        raise HTTPException(status_code=404, detail="mappings.json not found")
    with open(MAPPINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


@router.get("/api/ppt-text/{doc_id}")
def get_ppt_text(doc_id: str) -> dict:
    try:
        svc = ValueStreamService()
        raw_text = svc.get_ppt_text(doc_id=doc_id)
        cleaned = clean_ppt_text(raw_text)
        return {
            "doc_id": doc_id,
            "raw_preview": raw_text[:2000],
            "cleaned_preview": cleaned[:2000],
            "char_count": len(raw_text),
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
