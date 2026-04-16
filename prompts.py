"""
Prompt construction, loading, and LLM response JSON extraction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

import yaml
from pydantic import BaseModel, Field

from constants import RAG_PROMPTS_PATH

logger = logging.getLogger(__name__)

# --- JSON extraction

def safe_json_extract(text: str) -> dict:
    """Best effort extraction of a JSON object from LLM output."""
    text = (text or "").strip()

    # Direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Fenced JSON block
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # First/last braces
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass

    return {"selected_value_streams": [], "rejected_candidates": []}

# --- RAG prompts

def load_rag_prompts() -> Dict[str, str]:
    """Load the YAML prompt file for the RAG pipeline."""
    if not RAG_PROMPTS_PATH.exists():
        raise FileNotFoundError(f"Prompt file not found: {RAG_PROMPTS_PATH}")
    with open(RAG_PROMPTS_PATH, encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
    if "selection_user" not in payload and "user" in payload:
        payload["selection_user"] = payload["user"]
    missing = [k for k in ("system", "selection_user") if k not in payload]
    if missing:
        raise ValueError(f"Missing prompt keys in {RAG_PROMPTS_PATH}: {missing}")
    return payload

def render_prompt(template: str, **kwargs: Any) -> str:
    return template.format(**kwargs)


# --- Plain-approach structured output schema


class SelectedValueStream(BaseModel):
    entity_id: str = Field(description="The entity ID of the value stream")
    entity_name: str = Field(description="The name of the value stream")
    confidence: float = Field(description="0.8-1.0 strong direct alignment, 0.5-0.7 partial, 0.3-0.4 weak but plausible")
    reason: str = Field(description="Brief: which criteria matched and how")


class SelectionResult(BaseModel):
    selected_value_streams: List[SelectedValueStream] = Field(
        description="Value streams that are relevant to the idea card"
    )


# --- Plain-approach prompt (split: system is cacheable, user is per-request)

def build_selection_system_prompt(min_select: int = 10, max_select: int = 14) -> str:
    return f"""\
Role: Senior Healthcare Business Analyst specializing in value stream classification.

Task: Select the value streams from the candidate set that are relevant to the healthcare idea card summary.

A value stream is a business signal or business area materially touched by the idea, such as a \
function, capability, workflow, transformation theme, stakeholder space, or outcome area.

Use semantic business reasoning, not keyword matching. Infer the business intent, problem being \
solved, affected stakeholders, impacted functions/workflows, and expected outcomes.

A value stream is relevant when there is a defensible connection through one or more of:
1. Problem or pain-point overlap
2. Same or adjacent domain
3. Stakeholder overlap
4. Process, workflow, or capability connection
5. Outcome alignment
6. Upstream, downstream, enabling, or indirect transformation-theme connection

Prioritize recall, but do not over-select.
- Usually return between {min_select} and {max_select} value streams when enough plausible matches exist
- Return fewer if fewer are truly defensible
- Return at most {max_select} unless the schema or calling logic explicitly allows more
- Favor the strongest and most material business connections

Confidence:
- 0.8-1.0 = strong direct alignment
- 0.5-0.7 = partial but meaningful relevance
- 0.3-0.4 = weak but plausible relevance

Reason:
Provide a brief concrete business reason for each selected value stream.

Retrieval scores are supporting context only. Do not require exact wording overlap.

Output only data matching the provided schema."""


def build_plain_selection_prompt(query: str, context: str) -> str:
    """Build the user-message portion of the selection prompt (data only)."""
    return f"""IDEA CARD:

{query}

CANDIDATE VALUE STREAMS (evaluate every one):

{context}"""
