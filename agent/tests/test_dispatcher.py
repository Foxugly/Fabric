from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.domain.provider import Provider


class ExecuteOnlyProvider(Provider):
    name = "execute_only"

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_status(self) -> dict[str, Any]:
        return {"ready": True}

    async def get_capabilities(self) -> list[str]:
        return ["execute.only"]

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok"}

    def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError


class StubRegistry:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, name: str) -> Provider:
        return self._provider


@pytest.mark.asyncio
async def test_dispatcher_tolerates_provider_without_stream() -> None:
    dispatcher = CommandDispatcher(StubRegistry(ExecuteOnlyProvider()))  # type: ignore[arg-type]

    events = [event async for event in dispatcher.stream("execute_only", "noop", {})]
    result = await dispatcher.execute("execute_only", "noop", {})

    assert events == []
    assert result == {"status": "ok"}
