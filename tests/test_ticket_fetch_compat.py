from __future__ import annotations

import asyncio

from vs_app.integrations.jira.fetch_compat import get_ticket_data_compat


class _LegacyClient:
    async def get_ticket_data(self, ticket_id: str, config: object | None = None) -> dict:
        return {"ticket_id": ticket_id, "config": config}


class _ModernClient:
    async def get_ticket_data(
        self,
        ticket_id: str,
        config: object | None = None,
        llm_client: object | None = None,
    ) -> dict:
        return {"ticket_id": ticket_id, "config": config, "llm_client": llm_client}


def test_get_ticket_data_compat_handles_legacy_client_without_llm_kwarg() -> None:
    payload = asyncio.run(
        get_ticket_data_compat(
            _LegacyClient(),
            "IDMT-19761",
            config={"env": "test"},
            llm_client=object(),
        )
    )

    assert payload == {"ticket_id": "IDMT-19761", "config": {"env": "test"}}


def test_get_ticket_data_compat_preserves_llm_kwarg_for_modern_client() -> None:
    llm_client = object()
    payload = asyncio.run(
        get_ticket_data_compat(
            _ModernClient(),
            "IDMT-19761",
            config={"env": "test"},
            llm_client=llm_client,
        )
    )

    assert payload == {
        "ticket_id": "IDMT-19761",
        "config": {"env": "test"},
        "llm_client": llm_client,
    }
