from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from shared.protocol import local_action

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
        return await provider.execute(local_action(provider_name, action), payload)

    async def cancel(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        provider = self._registry.get(provider_name)
        await provider.cancel(local_action(provider_name, action), payload)

    async def stream(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        provider = self._registry.get(provider_name)
        try:
            stream = provider.stream(local_action(provider_name, action), payload)
        except NotImplementedError:
            return
        # `aclosing` guarantees the provider generator is finalised even when the
        # consumer stops early: providers hold session locks across yields.
        try:
            async with contextlib.aclosing(stream) as events:
                async for event in events:
                    yield event
        except NotImplementedError:
            return
