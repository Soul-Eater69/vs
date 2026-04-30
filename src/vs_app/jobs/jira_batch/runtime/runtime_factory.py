from __future__ import annotations

import logging
from typing import Any

from ..config import JiraIngestionConfig
from vs_app.integrations.clients.embedding import EmbeddingClient
from vs_app.integrations.clients.llm import IDPChatOpenAI
from vs_app.settings import EMBEDDING_DIMENSION, EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# Backward-compatible aliases (legacy name, direct implementation)
OpenAICompatibleLLM = IDPChatOpenAI
OpenAICompatibleEmbeddingClient = EmbeddingClient

def _set_if_present(cfg: JiraIngestionConfig, name: str, value: Any) -> None:
    if hasattr(cfg, name):
        setattr(cfg, name, value)


def build_ingestion_config(
    *,
    llm_model: str = "gpt-5-mini-idp",
    embedding_model: str = EMBEDDING_MODEL,
    skip_llm_summary: bool = True,
    skip_llm_keywords: bool = False,
    skip_llm_derived: bool = True,
) -> JiraIngestionConfig:
    cfg = JiraIngestionConfig(
        llm_model=llm_model,
        embedding_model=embedding_model,
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


def try_build_llm(*, enable: bool = True, model: str = "gpt-5-mini-idp") -> IDPChatOpenAI | None:
    if not enable:
        logger.info("LLM disabled")
        return None
    try:
        return IDPChatOpenAI(model=model)
    except Exception as exc:
        logger.warning("LLM unavailable (%s) - continuing without LLM", exc)
        return None


def try_build_embedding_client(
    *,
    enable: bool = True,
    model: str = EMBEDDING_MODEL,
    dimension: int = EMBEDDING_DIMENSION,
) -> EmbeddingClient | None:
    if not enable:
        logger.info("Embeddings disabled")
        return None
    try:
        return EmbeddingClient(model=model, dimension=dimension)
    except Exception as exc:
        logger.warning("Embedding client unavailable (%s) - continuing without embeddings", exc)
        return None
