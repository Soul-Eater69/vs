from __future__ import annotations

from vs_app.integrations.clients.llm import build_extra_body


def test_build_extra_body_keeps_api_version_without_reasoning_effort() -> None:
    assert build_extra_body() == {"api_version": "2024-04-01-preview"}


def test_build_extra_body_adds_reasoning_effort_when_configured() -> None:
    assert build_extra_body(reasoning_effort="high") == {
        "api_version": "2024-04-01-preview",
        "reasoning_effort": "high",
    }
