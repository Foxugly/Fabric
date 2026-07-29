from __future__ import annotations

import pytest

from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.application.registry import ProviderRegistry


@pytest.mark.asyncio
async def test_echo_provider_streams_progress() -> None:
    registry = ProviderRegistry()
    dispatcher = CommandDispatcher(registry)

    chunks: list[str] = []
    async for event in dispatcher.stream(
        "echo", "echo.message.send", {"text": "hello"}
    ):
        chunks.append(str(event["delta"]))

    assert "".join(chunks) == "Echo: hello"


@pytest.mark.asyncio
async def test_echo_provider_execute_returns_text() -> None:
    registry = ProviderRegistry()
    dispatcher = CommandDispatcher(registry)

    result = await dispatcher.execute("echo", "echo.message.send", {"text": "hello"})

    assert result == {"text": "Echo: hello"}


def test_provider_registry_exposes_claude_code_local() -> None:
    registry = ProviderRegistry()

    assert "claude_code_local" in registry.names()
    assert "windows_powershell" in registry.names()
