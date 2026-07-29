from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from websockets.asyncio.client import ClientConnection

from fabric_agent.application.config import AgentConfig
from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.application.registry import ProviderRegistry
from fabric_agent.transport.client import AgentWebSocketClient


class FakeRegistry:
    def items(self) -> list[tuple[str, object]]:
        return []


class FailingDispatcher:
    async def execute(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raise RuntimeError("boom")

    async def stream(
        self,
        provider_name: str,
        action: str,
        payload: dict[str, Any],
    ) -> Any:
        if False:
            yield {}


class CapturingClient(AgentWebSocketClient):
    def __init__(self) -> None:
        super().__init__(
            AgentConfig(
                server_ws_url="ws://example.invalid/ws/v1/agent/",
                agent_id="agent-1",
                agent_token="token-1",
            ),
            cast(ProviderRegistry, FakeRegistry()),
            cast(CommandDispatcher, FailingDispatcher()),
        )
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    async def _send_message(
        self,
        websocket: Any,
        message_type: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        self.messages.append((message_type, correlation_id, payload))


@pytest.mark.asyncio
async def test_handle_message_reports_command_failed_on_dispatch_error() -> None:
    client = CapturingClient()

    await client._handle_message(
        websocket=cast(ClientConnection, None),
        message_type="command.request",
        correlation_id="corr-1",
        payload={
            "command_id": "cmd-1",
            "provider": "claude_code_local",
            "action": "claude_code_local.message.send",
            "parameters": {"text": "hello"},
        },
    )
    await asyncio.sleep(0)

    assert client.messages == [
        ("command.accepted", "corr-1", {"command_id": "cmd-1"}),
        ("command.started", "corr-1", {"command_id": "cmd-1"}),
        (
            "command.failed",
            "corr-1",
            {"command_id": "cmd-1", "error": "boom"},
        ),
    ]
