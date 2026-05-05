from __future__ import annotations

import pytest

from vs_app.modules.ingestion.summary import pipeline


class Cfg:
    embedding_model = "test-embedding"


def test_embedding_failure_raises(monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ValueError("boom")

    monkeypatch.setattr(pipeline, "embed_batch", fail)

    with pytest.raises(RuntimeError, match="Embedding failed"):
        pipeline._embed("summary", object(), Cfg())


def test_empty_embedding_result_raises(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "embed_batch", lambda *args, **kwargs: [[]])

    with pytest.raises(RuntimeError, match="empty result"):
        pipeline._embed("summary", object(), Cfg())
