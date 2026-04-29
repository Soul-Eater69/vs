from __future__ import annotations

from vs_app.modules.rag.augmentation.prompt_context import build_system_prompt


def test_historical_prompt_keeps_distinct_confirmed_workflows() -> None:
    prompt = build_system_prompt()

    assert "confirmed_direct candidates" in prompt
    assert "Do not drop a distinct downstream workflow" in prompt
    assert "do not reject Appeal Decision merely because Adjudicate Claim" in prompt
