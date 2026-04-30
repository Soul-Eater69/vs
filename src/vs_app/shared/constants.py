"""Shared repo/runtime constants."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[3]

CANONICAL_VALUE_STREAMS: List[Dict[str, Any]] = []
PRECHUNK_DIR: Path = Path("jira_prechunk_output")
PROMPT_YAML_DIR: Path = _REPO_ROOT / "prompt_yaml"

__all__ = [
    "CANONICAL_VALUE_STREAMS",
    "PRECHUNK_DIR",
    "PROMPT_YAML_DIR",
]
