"""
Pipeline configuration - passed explicitly by the caller, no env loading.

Usage:
    from jira_ingestion import JiraIngestionConfig

    config = JiraIngestionConfig()                      # all defaults
    config = JiraIngestionConfig(max_slides=40, ocr_enabled=False)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class JiraIngestionConfig:
    max_slides: int = 60
    max_supplementary: int = 2
    ocr_enabled: bool = True
    enable_standalone_image_ocr: bool = True
    max_pages: int = 60
    max_prefetch_attachments: Optional[int] = 8
    max_prefetch_attachment_size: int = 60_000_000

    embedding_model: str = "text-embedding-3-large"
    llm_model: str = "gpt-5-mini-idp"

    entity_dict_path: str = "data/entity_dicts"

    min_file_size_bytes: int = 15_000
    min_pdf_size_bytes: int = 50_000
    max_file_size_bytes: int = 100_000_000
    layer1_skip_peak_score: int = 50
    layer1_skip_peek_gap: int = 20

    desc_junk_max_words: int = 10
    desc_thin_max_words: int = 50
    desc_rich_min_words: int = 150

    entity_confidence_text_match: float = 0.8
    entity_confidence_component_match: float = 1.0

    table_summary_cache_ttl: int = 7 * 24 * 3600

    jira_field_map: dict[str, str] = field(default_factory=lambda: {
        "business_unit": "customfield_10002",
        "product_area": "customfield_10003",
        "epic_link": "customfield_10014",
        "story_points": "customfield_10016",
        "sprint": "customfield_10020",
        "team": "customfield_10001",
        "epic_name": "customfield_10010",
        "impacted_products": "customfield_10040",
        "impacted_it_products": "customfield_10041",
        "requesting_org": "customfield_10050",
        "delivery_org": "customfield_10051",
        "product_stage": "customfield_10060",
    })

    enable_raw_artifact_persistence: bool = True
    enable_attachment_text_persistence: bool = True
    enable_debug_stage_persistence: bool = False
    enable_attachment_inventory: bool = True

    enable_retrieval_views: bool = True

    skip_llm_summary: bool = False
    skip_llm_keywords: bool = False
    skip_llm_derived: bool = False

    http_timeout_seconds: int = 120
    http_max_retries: int = 3
    http_retry_backoff_seconds: float = 1.5

    metadata_store_path: Optional[str] = None
    supervision_store_path: Optional[str] = None
