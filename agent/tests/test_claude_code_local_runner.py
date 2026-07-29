from __future__ import annotations

from pathlib import Path

import pytest

from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeCliError,
    ClaudeCodeCliRunner,
    ClaudeCodeExecutionRequest,
    _extract_result_text,
    _extract_session_id,
    _extract_text_delta,
    _parse_json_payload,
)


def test_build_json_command_with_resume() -> None:
    runner = ClaudeCodeCliRunner(Path("C:/Tools/claude.exe"))

    args = runner._build_json_command(
        ClaudeCodeExecutionRequest(
            prompt="Summarize this project",
            session_id="session-123",
        )
    )

    assert args == [
        "C:\\Tools\\claude.exe",
        "-p",
        "--output-format",
        "json",
        "--resume",
        "session-123",
        "Summarize this project",
    ]


def test_build_stream_command_without_resume() -> None:
    runner = ClaudeCodeCliRunner(Path("C:/Tools/claude.exe"))

    args = runner._build_stream_command(
        ClaudeCodeExecutionRequest(prompt="Explain recursion")
    )

    assert args == [
        "C:\\Tools\\claude.exe",
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "Explain recursion",
    ]


def test_parse_json_payload_rejects_invalid_json() -> None:
    with pytest.raises(ClaudeCodeCliError):
        _parse_json_payload("not-json", "")


def test_extract_helpers_handle_supported_shapes() -> None:
    payload = {"result": "done", "session_id": "session-123"}
    stream_event = {
        "type": "stream_event",
        "event": {"delta": {"type": "text_delta", "text": "hello"}},
    }

    assert _extract_result_text(payload) == "done"
    assert _extract_session_id(payload) == "session-123"
    assert _extract_text_delta(stream_event) == "hello"
