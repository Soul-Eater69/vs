from __future__ import annotations

import asyncio
import threading
import time

import pytest

from vs_app.ingestion.summary import pipeline
from vs_app.ingestion.summary.pipeline import ingest_ticket_summary_payload
from vs_app.modules.tickets.documents import TicketSummaryDocument


class JiraClient:
    async def download_attachment(self, att):
        return b""


class Cfg:
    skip_llm_summary = False
    strict_value_stream_classification = True
    max_documents = 4


class SkipCfg(Cfg):
    skip_llm_summary = True


@pytest.mark.anyio
async def test_llm_client_missing_raises() -> None:
    with pytest.raises(RuntimeError, match="LLM client is required"):
        await ingest_ticket_summary_payload(
            {"key": "IDMT-1", "fields": {"description": "source text"}},
            JiraClient(),
            llm_client=None,
            cfg=Cfg(),
        )


@pytest.mark.anyio
async def test_skip_llm_summary_raises() -> None:
    with pytest.raises(RuntimeError, match="skip_llm_summary=True"):
        await ingest_ticket_summary_payload(
            {"key": "IDMT-1", "fields": {"description": "source text"}},
            JiraClient(),
            llm_client=object(),
            cfg=SkipCfg(),
        )


@pytest.mark.anyio
async def test_sync_summary_work_runs_in_worker_threads(monkeypatch) -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def blocking_summary(ticket_id, consolidated_text, llm_client, cfg):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.1)
            return TicketSummaryDocument(
                ticket_id=ticket_id,
                summary_text="summary",
                business_problem="problem",
                business_capability="capability",
                key_terms=[],
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(pipeline, "summarize_ticket", blocking_summary)
    monkeypatch.setattr(pipeline, "classify_ticket_value_streams", lambda **kwargs: [])

    async def run(ticket_id: str):
        return await ingest_ticket_summary_payload(
            {"key": ticket_id, "fields": {"description": "source text"}},
            JiraClient(),
            llm_client=object(),
            cfg=Cfg(),
        )

    await asyncio.gather(run("IDMT-1"), run("IDMT-2"))

    assert max_active == 2
