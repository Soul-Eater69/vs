import asyncio
from types import SimpleNamespace

from vs_app.ingestion.chunks.chunk_builder import (
    extract_attachment_payload,
    prefetch_attachment_bytes,
)


def test_extract_attachment_payload_falls_back_to_generic_csv_chunks() -> None:
    cfg = SimpleNamespace(
        ocr_enabled=False,
        enable_raw_artifact_persistence=False,
        enable_attachment_text_persistence=False,
        enable_debug_stage_persistence=False,
        enable_prechunk_persistence=False,
    )

    payload = extract_attachment_payload(
        b"col1,col2\nfoo,bar\nbaz,qux\nlorem,ipsum\nhello,world\nmore,rows\n",
        {"filename": "sample.csv", "id": "att-1"},
        cfg,
    )

    assert payload["status"] == "extracted"
    assert payload["leaves"]
    assert payload["leaves"][0]["attachment_type"] == "csv"


def test_prefetch_attachment_bytes_treats_none_as_unlimited() -> None:
    class FakeClient:
        async def download_attachment(self, att: dict) -> bytes:
            return f"content:{att['id']}".encode("utf-8")

    attachments = [
        {"id": f"att-{idx}", "filename": f"file-{idx}.pdf", "size": 1024}
        for idx in range(10)
    ]
    cfg = SimpleNamespace(
        max_prefetch_attachments=None,
        max_prefetch_attachment_size=10_000_000,
    )

    prefetched = asyncio.run(prefetch_attachment_bytes(FakeClient(), attachments, cfg))

    assert len(prefetched) == 10
