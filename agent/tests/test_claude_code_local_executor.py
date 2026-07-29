from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest

from fabric_agent.providers.claude_code_local.detector import ClaudeCodeInstallation
from fabric_agent.providers.claude_code_local.executor import (
    ClaudeCodeMessageExecutor,
    ClaudeCodePayloadError,
    build_result_payload,
)
from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeExecutionResult,
    StreamDeltaEvent,
)


class InstallationProbe(Protocol):
    def probe(self) -> ClaudeCodeInstallation:
        ...


class StubInstallationProbe:
    def __init__(self, executable_path: Path | None) -> None:
        self._executable_path = executable_path

    def probe(self) -> ClaudeCodeInstallation:
        return ClaudeCodeInstallation(
            executable_path=self._executable_path,
            version="claude 2.1.212" if self._executable_path else None,
        )


class FakeMessageExecutor(ClaudeCodeMessageExecutor):
    def __init__(self) -> None:
        super().__init__(
            installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
        )

    async def execute(self, payload: dict[str, object]) -> ClaudeCodeExecutionResult:
        request = self._build_request(payload)
        return ClaudeCodeExecutionResult(
            text=f"Echo from Claude: {request.prompt}",
            session_id=request.session_id or "session-123",
            raw_payload={
                "result": f"Echo from Claude: {request.prompt}",
                "session_id": request.session_id or "session-123",
                "usage": {"input_tokens": 12, "output_tokens": 34},
            },
        )

    async def stream(
        self,
        payload: dict[str, object],
    ) -> tuple[list[StreamDeltaEvent], ClaudeCodeExecutionResult]:
        request = self._build_request(payload)
        return (
            [
                StreamDeltaEvent(sequence=1, delta="Hello", snapshot="Hello"),
                StreamDeltaEvent(sequence=2, delta=" world", snapshot="Hello world"),
            ],
            ClaudeCodeExecutionResult(
                text="Hello world",
                session_id=request.session_id or "session-123",
                raw_payload={"result": "Hello world", "session_id": "session-123"},
            ),
        )


def test_message_executor_validates_payload() -> None:
    executor = ClaudeCodeMessageExecutor(
        installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
    )

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": ""})

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": "ok", "timeout_seconds": 0})


def test_build_result_payload_sanitizes_metadata() -> None:
    result = ClaudeCodeExecutionResult(
        text="done",
        session_id="session-123",
        raw_payload={
            "result": "done",
            "session_id": "session-123",
            "usage": {"input_tokens": 12},
            "irrelevant": "ignored",
        },
    )

    payload = build_result_payload(result)

    assert payload == {
        "text": "done",
        "session_id": "session-123",
        "raw": {
            "session_id": "session-123",
            "usage": {"input_tokens": 12},
        },
    }
