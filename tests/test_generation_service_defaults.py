from __future__ import annotations

import importlib
import sys
from types import ModuleType


class _FakeIDPChatOpenAI:
    calls: list[dict] = []

    def __init__(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _load_generation_service(monkeypatch):
    fake_messages = ModuleType("langchain_core.messages")
    fake_messages.BaseMessage = object
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake_messages)

    fake_llm = ModuleType("vs_app.integrations.clients.llm")
    fake_llm.IDPChatOpenAI = _FakeIDPChatOpenAI
    fake_llm.build_extra_body = lambda *, reasoning_effort=None: {
        "api_version": "2024-04-01-preview",
        **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
    }
    monkeypatch.setitem(sys.modules, "vs_app.integrations.clients.llm", fake_llm)
    sys.modules.pop("vs_app.integrations.clients.generation_service", None)
    return importlib.import_module("vs_app.integrations.clients.generation_service")


def test_generation_service_defaults_to_gpt_5_mini_medium(monkeypatch) -> None:
    monkeypatch.delenv("GENERATION_LLM_MODEL", raising=False)
    monkeypatch.delenv("GENERATION_LLM_REASONING_EFFORT", raising=False)
    _FakeIDPChatOpenAI.calls.clear()

    module = _load_generation_service(monkeypatch)
    module.GenerationService(base_url="")

    assert _FakeIDPChatOpenAI.calls[-1]["model"] == "gpt-5-mini-idp"
    assert _FakeIDPChatOpenAI.calls[-1]["extra_body"]["reasoning_effort"] == "medium"


def test_generation_service_env_vars_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("GENERATION_LLM_MODEL", "custom-model")
    monkeypatch.setenv("GENERATION_LLM_REASONING_EFFORT", "low")
    _FakeIDPChatOpenAI.calls.clear()

    module = _load_generation_service(monkeypatch)
    module.GenerationService(base_url="")

    assert _FakeIDPChatOpenAI.calls[-1]["model"] == "custom-model"
    assert _FakeIDPChatOpenAI.calls[-1]["extra_body"]["reasoning_effort"] == "low"
