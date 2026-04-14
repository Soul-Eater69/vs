from __future__ import annotations

from typing import Optional


def _attachment_references(chunk: dict) -> list[dict]:
    attachment_id = str(chunk.get("attachment_id") or "")
    attachment_name = str(chunk.get("attachment_name") or "")
    if not attachment_id and not attachment_name:
        return []

    source_type = str(chunk.get("source") or "")
    if source_type.startswith("supplementary_"):
        category = "supplementary"
    elif source_type in ("pdf_page", "pptx_slide", "docx_section", "section"):
        category = "primary"
    else:
        category = "ticket"

    return [
        {
            "attachmentId": attachment_id,
            "attachmentName": attachment_name,
            "attachmentType": str(chunk.get("attachment_type") or ""),
            "category": category,
        }
    ]


def _content_type(chunk: dict) -> str:
    source = str(chunk.get("source") or "")
    if source in ("section", "docx_section"):
        return "section"
    if source in ("pdf_page", "pptx_slide"):
        return "page"
    if source.startswith("supplementary_"):
        return "supplementary"
    if source in ("description", "comment"):
        return source
    return source or "unknown"


def _resolve_page_range(chunk: dict) -> Optional[list[int]]:
    if chunk.get("page_range"):
        return chunk["page_range"]
    if chunk.get("source") == "pdf_page" and chunk.get("page_num") is not None:
        page_num = chunk["page_num"]
        return [page_num, page_num]
    return None


def _resolve_slide_range(chunk: dict) -> Optional[list[int]]:
    if chunk.get("slide_range"):
        return chunk["slide_range"]
    if chunk.get("source") == "pptx_slide" and chunk.get("slide_num") is not None:
        slide_num = chunk["slide_num"]
        return [slide_num, slide_num]
    return None


def build_chunk_record(chunk: dict, ticket_id: str, obs: dict, meta: dict, jira_base_url: str = "") -> dict:
    source_url = f"{jira_base_url.rstrip('/')}/browse/{ticket_id}" if jira_base_url else ticket_id
    content = str(chunk.get("text") or "")

    return {
        "id": str(chunk.get("chunk_uid") or chunk.get("chunk_id") or ""),
        "content": content,
        "contentVector": chunk.get("embedding") or None,
        "dataSource": "jira",
        "sourceId": ticket_id,
        "sourceUrl": source_url,
        "title": str(meta.get("title") or meta.get("summary") or ticket_id),
        "contentType": _content_type(chunk),
        "headerHierarchy": str(chunk.get("header_hierarchy") or chunk.get("section_title") or ""),
        "tokenCount": int(chunk.get("token_count") or chunk.get("word_count") or len(content.split())),
        "project": ticket_id.split("-")[0] if "-" in ticket_id else "",
        "issueType": str(meta.get("issue_type") or ""),
        "status": str(meta.get("status") or ""),
        "priority": str(meta.get("priority") or ""),
        "reporter": str(meta.get("reporter") or ""),
        "createdDate": str(obs.get("created") or ""),
        "updatedDate": str(obs.get("updated_at") or ""),
        "contentKeywords": chunk.get("content_keywords") or [],
        "attachmentReferences": _attachment_references(chunk),
        "chunkProvenance": {
            "chunkId": str(chunk.get("chunk_id") or ""),
            "chunkIndex": int(chunk.get("chunk_index") or 0),
            "sourceType": str(chunk.get("source") or ""),
            "attachmentId": str(chunk.get("attachment_id") or ""),
            "attachmentName": str(chunk.get("attachment_name") or ""),
            "attachmentType": str(chunk.get("attachment_type") or ""),
            "pageRange": _resolve_page_range(chunk),
            "slideRange": _resolve_slide_range(chunk),
            "extractionMethod": str(chunk.get("extraction_method") or ""),
            "extractionConfidence": chunk.get("extraction_confidence"),
        },
    }


def build_valuestream_record(ticket_id: str, result: dict) -> dict:
    supervision = result.get("supervision", {})
    observed = result.get("observed", {})
    meta = observed.get("metadata", {})

    impacted_products = supervision.get("impacted_products", {}) if isinstance(supervision.get("impacted_products"), dict) else {}
    impacted_it_products = supervision.get("impacted_it_products", {}) if isinstance(supervision.get("impacted_it_products"), dict) else {}

    return {
        "id": ticket_id,
        "ticketId": ticket_id,
        "project": ticket_id.split("-")[0] if "-" in ticket_id else "",
        "title": str(meta.get("title") or meta.get("summary") or ticket_id),
        "valueStreamIds": supervision.get("linked_value_stream_ids", []) or [],
        "valueStreamNames": supervision.get("linked_value_stream_names", []) or [],
        "valueStreamStatuses": supervision.get("linked_value_stream_statuses", []) or [],
        "impactedProductIds": impacted_products.get("ids", []) or [],
        "impactedProductNames": impacted_products.get("names", []) or [],
        "impactedItProductIds": impacted_it_products.get("ids", []) or [],
        "impactedItProductNames": impacted_it_products.get("names", []) or [],
        "labelSource": str(supervision.get("vs_label_source") or ""),
        "updatedDate": str(observed.get("updated_at") or ""),
    }


def project_ticket_outputs(ticket_id: str, result: dict, jira_base_url: str = "") -> tuple[list[dict], dict]:
    observed = result.get("observed", {})
    meta = observed.get("metadata", {})
    
    chunk_records = [
        build_chunk_record(chunk, ticket_id, observed, meta, jira_base_url=jira_base_url)
        for chunk in observed.get("chunks", [])
    ]
    
    vs_record = build_valuestream_record(ticket_id, result)
    
    return chunk_records, vs_record


def extract_retrieval_text(result: dict) -> str:
    return str(result.get("observed", {}).get("retrieval_text") or "")