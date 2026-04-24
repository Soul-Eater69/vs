from __future__ import annotations

import os
import pathlib

from core.constants import CANONICAL_VALUE_STREAMS, MAPPINGS_PATH, IDEA_CARDS_DIR, ROOT_DIR

TICKET_CHUNKS_DIR: pathlib.Path = pathlib.Path(
    os.environ.get("TICKET_CHUNKS_DIR", str(ROOT_DIR / "ticket_chunks"))
).resolve()

HISTORICAL_FAISS_DIR: pathlib.Path = pathlib.Path(
    os.environ.get("HISTORICAL_FAISS_DIR", "ticket_data/_faiss")
).resolve()

__all__ = [
    "CANONICAL_VALUE_STREAMS",
    "MAPPINGS_PATH",
    "IDEA_CARDS_DIR",
    "TICKET_CHUNKS_DIR",
    "HISTORICAL_FAISS_DIR",
]
