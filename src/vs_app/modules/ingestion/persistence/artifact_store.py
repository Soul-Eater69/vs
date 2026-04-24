"""Simple file-backed artifact persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ArtifactStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)

    def write_json(self, relative_path: str, payload: Any) -> Path:
        target = self.base_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target


__all__ = ["ArtifactStore"]
