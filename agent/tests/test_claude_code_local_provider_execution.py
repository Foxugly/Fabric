from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeSessionStatusDetector,
)
from fabric_agent.providers.claude_code_local.executor import (
    ClaudeCodeMessageExecutor,
)
from fabric_agent.providers.claude_code_local.models import SessionStatus
from fabric_agent.providers.claude_code_local.provider import ClaudeCodeLocalProvider
from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeExecutionResult,
    StreamChunk,
    StreamDeltaEvent,
)


class StubStatusDetector(ClaudeCodeSessionStatusDetector):
    def __init__(self, status: SessionStatus) -> None:
        self._status = status

    def detect(self) -> SessionStatus:
        return self._status


class FakeMessageExecutor(ClaudeCodeMessageExecutor):
    async def execute(self, payload: dict[str, Any]) -> ClaudeCodeExecutionResult:
        return ClaudeCodeExecutionResult(
            text="Final response",
            session_id="session-123",
            raw_payload={"result": "Final response", "session_id": "session-123"},
        )

    async def stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncGenerator[StreamChunk, None]:
        yield StreamDeltaEvent(sequence=1, delta="Final", snapshot="Final")
        yield StreamDeltaEvent(
            sequence=2,
            delta=" response",
            snapshot="Final response",
        )
        yield ClaudeCodeExecutionResult(
            text="Final response",
            session_id="session-123",
            raw_payload={"result": "Final response", "session_id": "session-123"},
        )


class BlockingMessageExecutor(ClaudeCodeMessageExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    async def execute(self, payload: dict[str, Any]) -> ClaudeCodeExecutionResult:
        text = str(payload["text"])
        self.calls.append(text)
        self.started.set()
        await self.release.wait()
        return ClaudeCodeExecutionResult(
            text=text,
            session_id="session-123",
            raw_payload={"result": text, "session_id": "session-123"},
        )

    async def cancel(self, payload: dict[str, Any]) -> None:
        self.cancelled.append(str(payload.get("_fabric_command_id", "")))


@pytest.mark.asyncio
async def test_provider_executes_session_status_action() -> None:
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(
            SessionStatus(
                session_detected=True,
                session_ready=True,
                manual_action_required=False,
                transport="local_session",
            )
        ),
        message_executor=FakeMessageExecutor(),
    )

    result = await provider.execute("session.status", {})

    assert result["session_detected"] is True


@pytest.mark.asyncio
async def test_provider_streams_and_reuses_cached_final_result() -> None:
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(
            SessionStatus(
                session_detected=True,
                session_ready=True,
                manual_action_required=False,
                transport="local_session",
            )
        ),
        message_executor=FakeMessageExecutor(),
    )
    payload = {"text": "hello"}

    deltas = [event async for event in provider.stream("message.send", payload)]
    result = await provider.execute("message.send", payload)

    assert deltas == [
        {
            "event": "message.delta",
            "sequence": 1,
            "delta": "Final",
            "snapshot": "Final",
        },
        {
            "event": "message.delta",
            "sequence": 2,
            "delta": " response",
            "snapshot": "Final response",
        },
    ]
    assert result["text"] == "Final response"
    assert result["session_id"] == "session-123"


@pytest.mark.asyncio
async def test_provider_serializes_concurrent_message_send_on_same_session() -> None:
    executor = BlockingMessageExecutor()
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(
            SessionStatus(
                session_detected=True,
                session_ready=True,
                manual_action_required=False,
                transport="local_session",
            )
        ),
        message_executor=executor,
    )

    first_task = asyncio.create_task(
        provider.execute("message.send", {"text": "first", "session_id": "shared"})
    )
    await executor.started.wait()

    second_task = asyncio.create_task(
        provider.execute("message.send", {"text": "second", "session_id": "shared"})
    )
    await asyncio.sleep(0.05)
    assert executor.calls == ["first"]

    executor.release.set()
    first_result = await first_task
    second_result = await second_task

    assert first_result["text"] == "first"
    assert second_result["text"] == "second"
    assert executor.calls == ["first", "second"]


@pytest.mark.asyncio
async def test_provider_cancel_stops_the_running_cli() -> None:
    executor = BlockingMessageExecutor()
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(_ready_status()),
        message_executor=executor,
    )

    await provider.cancel("message.send", {"_fabric_command_id": "cmd-1"})

    assert executor.cancelled == ["cmd-1"]


@pytest.mark.asyncio
async def test_provider_advertises_fully_qualified_capabilities() -> None:
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(_ready_status()),
        message_executor=FakeMessageExecutor(),
    )

    capabilities = await provider.get_capabilities()

    assert "claude_code_local.message.send" in capabilities
    assert "claude_code_local.session.status" in capabilities


@pytest.mark.asyncio
async def test_provider_releases_session_lock_when_stream_abandoned() -> None:
    provider = ClaudeCodeLocalProvider(
        status_detector=StubStatusDetector(_ready_status()),
        message_executor=FakeMessageExecutor(),
    )
    payload = {"text": "hello", "session_id": "shared"}

    stream = provider.stream("message.send", payload)
    assert await anext(stream) is not None
    await stream.aclose()

    # The abandoned stream must not keep the per-session lock forever.
    result = await asyncio.wait_for(
        provider.execute("message.send", payload), timeout=1
    )

    assert result["text"] == "Final response"


def _ready_status() -> SessionStatus:
    return SessionStatus(
        session_detected=True,
        session_ready=True,
        manual_action_required=False,
        transport="local_session",
    )
