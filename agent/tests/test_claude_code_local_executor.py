from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Protocol

import pytest

from fabric_agent.providers.claude_code_local.detector import ClaudeCodeInstallation
from fabric_agent.providers.claude_code_local.executor import (
    ClaudeCodeMessageExecutor,
    ClaudeCodePayloadError,
    build_result_payload,
)
from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeExecutionRequest,
    ClaudeCodeExecutionResult,
    StreamChunk,
    StreamDeltaEvent,
)

SESSION_ID = "0f8e2a3c-5b1d-4c6e-9a7f-1234567890ab"


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

    async def execute(self, payload: dict[str, Any]) -> ClaudeCodeExecutionResult:
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
        payload: dict[str, Any],
    ) -> AsyncGenerator[StreamChunk, None]:
        request = self._build_request(payload)
        yield StreamDeltaEvent(sequence=1, delta="Hello", snapshot="Hello")
        yield StreamDeltaEvent(sequence=2, delta=" world", snapshot="Hello world")
        yield ClaudeCodeExecutionResult(
            text="Hello world",
            session_id=request.session_id or "session-123",
            raw_payload={"result": "Hello world", "session_id": "session-123"},
        )


def test_message_executor_validates_payload() -> None:
    executor = ClaudeCodeMessageExecutor(
        installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
    )

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": ""})

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": "ok", "timeout_seconds": 0})

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": "ok", "permission_mode": "yolo"})

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": "ok", "allowed_tools": "Bash"})


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "--dangerously-skip-permissions"},
        {"session_id": "session-123"},
        {"session_id": "0f8e2a3c-5b1d-4c6e-9a7f-1234567890ab\n"},
        {"model": "--dangerously-skip-permissions"},
        {"model": "sonnet --verbose"},
        {"allowed_tools": ["--dangerously-skip-permissions"]},
        {"disallowed_tools": ["--dangerously-skip-permissions"]},
        {"disallowed_tools": ["Bash", "-p"]},
        {"disallowed_tools": ["Bash,Edit"]},
        {"disallowed_tools": ["Bash\nEdit"]},
        {"disallowed_tools": ["Bash"] * 65},
    ],
)
def test_message_executor_refuses_values_that_could_become_cli_options(
    payload: dict[str, Any],
) -> None:
    executor = ClaudeCodeMessageExecutor(
        installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
    )

    with pytest.raises(ClaudeCodePayloadError):
        executor._build_request({"text": "ok", **payload})


def test_message_executor_maps_claude_cli_options() -> None:
    executor = ClaudeCodeMessageExecutor(
        installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
    )

    request = executor._build_request(
        {
            "text": "ship it",
            "session_id": SESSION_ID,
            "permission_mode": "acceptEdits",
            "model": "sonnet",
            "allowed_tools": ["Bash(git *)", "Edit"],
            "disallowed_tools": ["mcp__github__create_issue", "Read(./secrets/**)"],
            "_fabric_timeout_seconds": 900,
        }
    )

    assert request.session_id == SESSION_ID
    assert request.permission_mode == "acceptEdits"
    assert request.model == "sonnet"
    assert request.allowed_tools == ("Bash(git *)", "Edit")
    assert request.disallowed_tools == (
        "mcp__github__create_issue",
        "Read(./secrets/**)",
    )
    assert request.timeout_seconds == 900


def test_message_executor_defaults_to_a_20_minute_turn() -> None:
    executor = ClaudeCodeMessageExecutor(
        installation_probe=StubInstallationProbe(Path("C:/Tools/claude.exe"))
    )

    request = executor._build_request({"text": "ship it"})

    assert request.timeout_seconds == 1200
    assert ClaudeCodeExecutionRequest(prompt="x").timeout_seconds == 1200


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
