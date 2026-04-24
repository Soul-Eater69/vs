"""
Shared constants used across pipelines.

TODO: CANONICAL_VALUE_STREAMS and PRECHUNK_DIR were previously imported from
core.constants which never existed in the repo. Populate these with real values.
"""

from __future__ import annotations

import pathlib
from typing import Any, Dict, List

# Canonical value stream definitions — list of dicts with at least {"name": ...}
CANONICAL_VALUE_STREAMS: List[Dict[str, Any]] = []

# Default directory for pre-chunked ticket output
PRECHUNK_DIR: pathlib.Path = pathlib.Path("jira_prechunk_output")

# Root directory for application prompt YAML files.
PROMPT_YAML_DIR: pathlib.Path = pathlib.Path(__file__).parent / "prompt_yaml"

# Path to the primary RAG selection prompt YAML file.
RAG_PROMPTS_PATH: pathlib.Path = PROMPT_YAML_DIR / "rag_selection.yaml"
