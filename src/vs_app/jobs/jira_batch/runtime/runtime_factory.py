from __future__ import annotations

from typing import Any

from ..config import JiraIngestionConfig
from vs_app.settings import EMBEDDING_MODEL

def _set_if_present(cfg: JiraIngestionConfig, name: str, value: Any) -> None:
    if hasattr(cfg, name):
        setattr(cfg, name, value)


def build_ingestion_config(
    *,
    llm_model: str = "gpt-5-mini-idp",
    embedding_model: str = EMBEDDING_MODEL,
    llm_max_output_tokens: int | None = None,
    summary_input_char_limit: int = 20_000,
    classification_input_char_limit: int = 20_000,
    enable_llm_prompt_sanitization_retry: bool = True,
    skip_llm_summary: bool = False,
    skip_llm_keywords: bool = False,
    skip_llm_derived: bool = True,
) -> JiraIngestionConfig:
    cfg = JiraIngestionConfig(
        llm_model=llm_model,
        embedding_model=embedding_model,
        llm_max_output_tokens=llm_max_output_tokens,
        summary_input_char_limit=summary_input_char_limit,
        classification_input_char_limit=classification_input_char_limit,
        enable_llm_prompt_sanitization_retry=enable_llm_prompt_sanitization_retry,
        enable_raw_artifact_persistence=False,
        enable_attachment_text_persistence=False,
        enable_debug_stage_persistence=False,
        enable_attachment_inventory=True,
        enable_retrieval_views=True,
        skip_llm_summary=skip_llm_summary,
        skip_llm_keywords=skip_llm_keywords,
        skip_llm_derived=skip_llm_derived,
    )

    _set_if_present(cfg, "include_section_rollups_in_retrieval", False)
    _set_if_present(cfg, "max_prefetch_attachments", None)

    return cfg

