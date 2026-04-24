"""Compatibility helpers for calling ticket fetchers with mixed signatures."""

from __future__ import annotations

import inspect
from typing import Any


async def get_ticket_data_compat(
    ticket_client: Any,
    ticket_id: str,
    *,
    config: Any = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Call get_ticket_data across old and new client signatures."""
    fetch = ticket_client.get_ticket_data
    kwargs = _supported_fetch_kwargs(fetch, config=config, llm_client=llm_client)
    if not kwargs:
        return await fetch(ticket_id)

    try:
        return await fetch(ticket_id, **kwargs)
    except TypeError as exc:
        fallback_kwargs = _strip_unexpected_kwargs(kwargs, str(exc))
        if fallback_kwargs == kwargs:
            raise
        if fallback_kwargs:
            return await fetch(ticket_id, **fallback_kwargs)
        return await fetch(ticket_id)


def _supported_fetch_kwargs(
    fetch: Any,
    *,
    config: Any = None,
    llm_client: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    supports_var_kwargs = False
    supported_names: set[str] = set()

    try:
        signature = inspect.signature(fetch)
    except (TypeError, ValueError):
        signature = None

    if signature is not None:
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                supports_var_kwargs = True
            else:
                supported_names.add(parameter.name)

    if config is not None and (supports_var_kwargs or not supported_names or "config" in supported_names):
        kwargs["config"] = config
    if llm_client is not None and (
        supports_var_kwargs or not supported_names or "llm_client" in supported_names
    ):
        kwargs["llm_client"] = llm_client
    return kwargs


def _strip_unexpected_kwargs(kwargs: dict[str, Any], error_message: str) -> dict[str, Any]:
    fallback_kwargs = dict(kwargs)
    if "unexpected keyword argument 'llm_client'" in error_message:
        fallback_kwargs.pop("llm_client", None)
    if "unexpected keyword argument 'config'" in error_message:
        fallback_kwargs.pop("config", None)
    return fallback_kwargs
