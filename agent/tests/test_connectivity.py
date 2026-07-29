from __future__ import annotations

from collections.abc import Awaitable
from types import SimpleNamespace
from typing import Any

import pytest

from fabric_agent.application.config import AgentConfig
from fabric_agent.transport.connectivity import ConnectivityWatchdog


class FakeWebSocket:
    def __init__(self) -> None:
        self.state = SimpleNamespace(name="OPEN")
        self.closed = False

    async def ping(self) -> Awaitable[None]:
        async def _pong() -> None:
            return None

        return _pong()

    async def close(self, code: int, reason: str) -> None:
        self.closed = True


class FailingWatchdog(ConnectivityWatchdog):
    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self.calls = 0

    async def _check(self, websocket: Any) -> bool:
        self.calls += 1
        return False


@pytest.mark.asyncio
async def test_connectivity_watchdog_closes_websocket_after_threshold() -> None:
    websocket = FakeWebSocket()
    watchdog = FailingWatchdog(
        AgentConfig(
            server_ws_url="ws://127.0.0.1:8000/ws/v1/agent/",
            agent_id="agent",
            agent_token="token",
            connectivity_check_seconds=0,
            connectivity_failure_threshold=1,
        )
    )

    await watchdog.monitor(websocket)  # type: ignore[arg-type]

    assert websocket.closed is True
    assert watchdog.calls == 1
