"""Debug artifact writer built on the canonical artifact store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore


class DebugWriter:
    def __init__(self, base_dir: str | Path) -> None:
        self.store = ArtifactStore(base_dir)

    def write_json(self, relative_path: str, payload: Any) -> Path:
        return self.store.write_json(relative_path, payload)


__all__ = ["DebugWriter"]
