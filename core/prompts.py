"""
Prompt construction, loading, and LLM response JSON extraction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List

import yaml

from .constants import RAG_PROMPTS_PATH

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
    missing = [k for k in ("system", "selection_user") if k not in payload]
    if missing:
        raise ValueError(f"Missing prompt keys in {RAG_PROMPTS_PATH}: {missing}")
    return payload

def render_prompt(template: str, **kwargs: Any) -> str:
    return template.format(**kwargs)


# --- Plain-approach prompt

def build_plain_selection_prompt(query: str, context: str) -> str:
    return f"""
----------------------------------------------------------------------
TASK: Select value streams for an idea card

You are classifying a healthcare idea card into one or more value streams.

In this task, a value stream is NOT just a strict workflow label.
A value stream is a business signal that indicates a meaningful area of relevance for the idea.
It may represent a business function, capability area, operational process, transformation theme,
stakeholder space, or outcome area that the idea materially touches.

You should think like a business analyst while making decisions.
That means:
- infer the business intent of the idea
- identify the problem the idea is trying to solve
- identify which business areas, functions, stakeholders, or outcomes are affected
- select all value streams that are plausibly relevant, even when the connection is indirect,
  partial, or implied rather than explicitly stated

PRIORITY: High recall.
Include every value stream that is plausibly relevant.
It is worse to miss a correct match than to include a marginal one.
----------------------------------------------------------------------

DECISION CRITERIA (evaluate each candidate against these):

1. BUSINESS PROBLEM ALIGNMENT:
   Does the idea card's problem, pain point, inefficiency, risk, or opportunity overlap with
   what this value stream is relevant to? Even partial overlap counts.

2. DOMAIN OR FUNCTIONAL RELEVANCE:
   Does the idea card operate in the same or adjacent clinical, operational, administrative,
   financial, digital, service, or business domain as this value stream?

3. STAKEHOLDER OVERLAP:
   Are the people, roles, teams, departments, providers, patients, members, or populations
   mentioned in the idea card meaningfully connected to this value stream?

4. PROCESS OR CAPABILITY CONNECTION:
   Does the idea affect a process, workflow, capability, or business area represented by this
   value stream?

5. OUTCOME CONNECTION:
   Could this value stream's goals, KPIs, efficiency targets, experience goals, compliance goals,
   quality goals, or transformation outcomes be advanced by the idea card's proposed work?

Select a candidate if ANY of these criteria show meaningful alignment.
Reject only if NONE of the criteria apply.
----------------------------------------------------------------------

INTERPRETATION RULES:

- Use semantic meaning and business context, not just keyword matching.
- A value stream does not need to be explicitly named in the idea card to be relevant.
- When uncertain, prefer select — but you must be able to articulate a concrete
  business connection in the reason field. "Might be related" is not sufficient.
- Assign confidence scores honestly:
    0.8-1.0 = strong, direct alignment on problem + domain
    0.5-0.7 = clear but partial alignment (e.g. shared domain, adjacent process)
    0.3-0.4 = weak but plausible connection (include for recall, flag low confidence)
----------------------------------------------------------------------

IDEA CARD CONTENT:

{query}

----------------------------------------------------------------------

CANDIDATE VALUE STREAMS (evaluate every one):

{context}

----------------------------------------------------------------------

For each candidate, decide: select or reject.

Return JSON:
{{
  "selected_value_streams": [
    {{
      "entity_id": "string",
      "entity_name": "string",
      "confidence": 0.0,
      "reason": "which criteria matched and why this value stream is a relevant business signal for the idea"
    }}
  ],
  "rejected_candidates": [
    {{
      "entity_id": "string",
      "entity_name": "string",
      "reason": "why this candidate has no meaningful business relevance to the idea"
    }}
  ]
}}
----------------------------------------------------------------------
""".strip()