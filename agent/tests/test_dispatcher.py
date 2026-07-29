from __future__ import annotations

from collections.abc import AsyncGenerator
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
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError


class StubRegistry:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider

    def get(self, name: str) -> Provider:
        return self._provider


class RecordingProvider(ExecuteOnlyProvider):
    name = "claude_code_local"

    def __init__(self) -> None:
        self.actions: list[str] = []

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.actions.append(action)
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_dispatcher_tolerates_provider_without_stream() -> None:
    dispatcher = CommandDispatcher(StubRegistry(ExecuteOnlyProvider()))  # type: ignore[arg-type]

    events = [event async for event in dispatcher.stream("execute_only", "noop", {})]
    result = await dispatcher.execute("execute_only", "noop", {})

    assert events == []
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_dispatcher_strips_the_provider_namespace_from_actions() -> None:
    provider = RecordingProvider()
    dispatcher = CommandDispatcher(StubRegistry(provider))  # type: ignore[arg-type]

    await dispatcher.execute(
        "claude_code_local", "claude_code_local.message.send", {}
    )
    await dispatcher.execute("claude_code_local", "message.send", {})

    assert provider.actions == ["message.send", "message.send"]
