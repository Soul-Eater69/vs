"""Persistence: local JSON + Azure Search upload."""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Dict, List, Optional

from ..models import EnrichedTicket

logger = logging.getLogger(__name__)

DEFAULT_STORE_PATH = pathlib.Path("historical_enriched.json")
HISTORICAL_INDEX_NAME = "historical-enriched-tickets"


# ---------------------------------------------------------------------------
# Local JSON
# ---------------------------------------------------------------------------

def save_json(
    tickets: List[EnrichedTicket],
    path: pathlib.Path = DEFAULT_STORE_PATH,
    merge: bool = True,
) -> int:
    """Save enriched tickets to JSON. Merges by ticket_id. Returns total count."""
    existing: Dict[str, dict] = {}
    if merge and path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data if isinstance(data, dict) else {
                t["ticket_id"]: t for t in data if isinstance(t, dict) and t.get("ticket_id")
            }
        except Exception as exc:
            logger.warning("[STORE] Could not load %s: %s", path, exc)

    for ticket in tickets:
        existing[ticket.ticket_id] = ticket.model_dump(mode="json")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    logger.info("[STORE] Saved %d tickets to %s", len(existing), path)
    return len(existing)


def load_json(path: pathlib.Path = DEFAULT_STORE_PATH) -> List[EnrichedTicket]:
    """Load enriched tickets from JSON."""
    if not path.exists():
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.values() if isinstance(data, dict) else data
    tickets = []
    for raw in records:
        try:
            tickets.append(EnrichedTicket.model_validate(raw))
        except Exception as exc:
            logger.warning("[STORE] Skipping invalid record: %s", exc)

    logger.info("[STORE] Loaded %d tickets from %s", len(tickets), path)
    return tickets


# ---------------------------------------------------------------------------
# Azure Search upload
# ---------------------------------------------------------------------------

def upload_to_index(
    tickets: List[EnrichedTicket],
    index_name: str = HISTORICAL_INDEX_NAME,
    embedding_client: Optional[object] = None,
) -> int:
    """Upload enriched tickets to Azure Search. Returns count uploaded."""
    documents = []
    for ticket in tickets:
        if ticket.enrichment_status != "enriched":
            continue

        embedding = ticket.summary_embedding
        if embedding is None and embedding_client is not None and ticket.summary:
            try:
                embedding = embedding_client.embed(ticket.summary)
            except Exception as exc:
                logger.warning("[INDEX] Embedding failed for %s: %s", ticket.ticket_id, exc)

        doc = {
            "ticket_id": ticket.ticket_id,
            "title": ticket.title,
            "summary": ticket.summary,
            "domain_tags": ticket.domain_tags,
            "value_stream_names": [vs.vs_name for vs in ticket.value_streams],
            "direct_vs_names": [vs.vs_name for vs in ticket.value_streams if vs.inference_type == "direct"],
            "implied_vs_names": [vs.vs_name for vs in ticket.value_streams if vs.inference_type == "implied"],
            "value_streams_json": json.dumps(
                [{"vs_name": vs.vs_name, "inference_type": vs.inference_type.value, "reason": vs.reason}
                 for vs in ticket.value_streams],
                ensure_ascii=False,
            ),
            "raw_text_preview": ticket.raw_text_preview,
            "char_count": ticket.char_count,
        }
        if embedding is not None:
            doc["summary_vector"] = embedding

        documents.append(doc)

    if not documents:
        logger.warning("[INDEX] No enriched documents to upload")
        return 0

    try:
        from ....clients.azure_direct_client import AzureDirectSearchClient
        client = AzureDirectSearchClient(index_name=index_name)
        uploaded = client.upload_documents(documents)
        logger.info("[INDEX] Uploaded %d to '%s'", uploaded, index_name)
        return uploaded
    except Exception as exc:
        fallback = pathlib.Path(f"historical_upload_{index_name}.json")
        with open(fallback, "w", encoding="utf-8") as f:
            json.dump(documents, f, ensure_ascii=False, indent=2)
        logger.warning("[INDEX] Upload failed (%s). Saved %d docs to %s", exc, len(documents), fallback)
        return 0
