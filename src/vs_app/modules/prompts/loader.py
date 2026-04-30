"""
Prompt construction, loading, and LLM response JSON extraction.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

from vs_app.shared.constants import PROMPT_YAML_DIR
# Re-exported for backward compatibility — canonical home is .schemas
from .schemas import SelectedValueStream, SelectionResult  # noqa: F401

logger = logging.getLogger(__name__)


def safe_json_extract(text: str) -> dict:
    """Best effort extraction of a JSON object from LLM output."""
    text = (text or "").strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass

    return {}


@lru_cache(maxsize=None)
def _load_yaml_payload(path_value: str) -> Dict[str, Any]:
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    if not isinstance(payload, dict):
        raise ValueError(f"Prompt file must contain a mapping: {path}")

    return payload


def load_prompt_yaml(name: str) -> Dict[str, Any]:
    path = PROMPT_YAML_DIR / f"{name}.yaml"
    return _load_yaml_payload(str(path))


def _load_required_prompt_yaml(name: str, required_keys: list[str]) -> Dict[str, Any]:
    payload = load_prompt_yaml(name)
    missing = [key for key in required_keys if key not in payload]
    if missing:
        raise ValueError(
            f"Missing prompt keys in {PROMPT_YAML_DIR / f'{name}.yaml'}: {missing}"
        )
    return payload


def render_prompt(template: str, **kwargs: Any) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(kwargs.get(key, match.group(0)))

    normalized = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", replace, template)
    return normalized.format(**kwargs)


def build_selection_system_prompt(min_select: int = 10, max_select: int = 14) -> str:
    payload = _load_required_prompt_yaml("selection", ["system"])
    return render_prompt(
        str(payload["system"]),
        min_select=min_select,
        max_select=max_select,
    )


def build_plain_selection_prompt(query: str, context: str) -> str:
    payload = _load_required_prompt_yaml("selection", ["user"])
    return render_prompt(str(payload["user"]), query=query, context=context)


def build_historical_selection_system_prompt(
    min_select: int = 6,
    max_select: int = 12,
) -> str:
    base_prompt = build_selection_system_prompt(
        min_select=min_select,
        max_select=max_select,
    ).rstrip()
    payload = _load_required_prompt_yaml("historical_rag_selection", ["system_extension"])
    extension = str(payload["system_extension"]).strip()
    return f"{base_prompt}\n\n{extension}" if extension else base_prompt


def build_historical_selection_prompt(
    query_for_prompt: str,
    candidate_blocks: str,
) -> str:
    payload = _load_required_prompt_yaml("historical_rag_selection", ["user"])
    return render_prompt(
        str(payload["user"]),
        query_for_prompt=query_for_prompt,
        candidate_blocks=candidate_blocks,
    )


def build_historical_gap_selection_prompt(
    *,
    query_for_prompt: str,
    precedent_context: str,
    faiss_hit_context: str,
    candidate_blocks: str,
) -> str:
    payload = _load_required_prompt_yaml("historical_gap_selection", ["user"])
    return render_prompt(
        str(payload["user"]),
        query_for_prompt=query_for_prompt,
        precedent_context=precedent_context,
        faiss_hit_context=faiss_hit_context,
        candidate_blocks=candidate_blocks,
    )


def build_value_stream_classification_prompt(
    *,
    ticket_id: str,
    text: str,
    value_streams: str,
) -> str:
    payload = _load_required_prompt_yaml(
        "value_stream_classification",
        ["schema", "template"],
    )
    return render_prompt(
        str(payload["template"]),
        ticket_id=ticket_id,
        text=text,
        value_streams=value_streams,
        value_stream_schema=str(payload["schema"]).strip(),
    )


def build_historical_enrichment_system_prompt() -> str:
    payload = _load_required_prompt_yaml("historical_enrichment", ["system"])
    return str(payload["system"]).strip()


def build_historical_enrichment_prompt(*, raw_text: str, vs_list: str) -> str:
    payload = _load_required_prompt_yaml("historical_enrichment", ["user"])
    return render_prompt(
        str(payload["user"]),
        raw_text=raw_text,
        vs_list=vs_list,
    )


def build_jira_value_stream_verifier_system_prompt() -> str:
    payload = _load_required_prompt_yaml("jira_value_stream_verifier", ["system"])
    return str(payload["system"]).strip()


def build_jira_value_stream_verifier_prompt(
    *,
    approved_value_streams: str,
    unresolved_block: str,
) -> str:
    payload = _load_required_prompt_yaml("jira_value_stream_verifier", ["user", "schema"])
    return render_prompt(
        str(payload["user"]),
        approved_value_streams=approved_value_streams,
        unresolved_block=unresolved_block,
        value_stream_schema=str(payload["schema"]).strip(),
    )


__all__ = [
    "SelectionResult",
    "SelectedValueStream",
    "build_historical_enrichment_prompt",
    "build_historical_enrichment_system_prompt",
    "build_historical_gap_selection_prompt",
    "build_historical_selection_prompt",
    "build_historical_selection_system_prompt",
    "build_jira_value_stream_verifier_prompt",
    "build_jira_value_stream_verifier_system_prompt",
    "build_plain_selection_prompt",
    "build_selection_system_prompt",
    "build_value_stream_classification_prompt",
    "load_prompt_yaml",
    "render_prompt",
    "safe_json_extract",
]
