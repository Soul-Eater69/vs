from __future__ import annotations

import pytest

from vs_app.ingestion.summary.pipeline import ingest_ticket_summary_payload


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
