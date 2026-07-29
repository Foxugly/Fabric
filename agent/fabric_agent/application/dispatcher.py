from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fabric_agent.application.registry import ProviderRegistry


class CommandDispatcher:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self._registry.get(provider_name)
        return await provider.execute(action, payload)

    async def cancel(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        provider = self._registry.get(provider_name)
        await provider.cancel(action, payload)

    async def stream(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        provider = self._registry.get(provider_name)
        try:
            stream = provider.stream(action, payload)
        except NotImplementedError:
            return
        try:
            async for event in stream:
                yield event
        except NotImplementedError:
            return
