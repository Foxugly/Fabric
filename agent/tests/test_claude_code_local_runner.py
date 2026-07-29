from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeCliError,
    ClaudeCodeCliRunner,
    ClaudeCodeExecutionRequest,
    ClaudeCodeExecutionResult,
    StreamDeltaEvent,
    _extract_progress,
    _extract_result_text,
    _extract_session_id,
    _extract_text_delta,
    _parse_json_payload,
)

#: The agent targets Windows, but the test suite also runs on Linux CI where
#: `str(Path(...))` keeps forward slashes. Compare against the resolved form.
CLAUDE_PATH = Path("C:/Tools/claude.exe")


def test_build_json_command_with_resume() -> None:
    runner = ClaudeCodeCliRunner(CLAUDE_PATH)

    args = runner._build_json_command(
        ClaudeCodeExecutionRequest(
            prompt="Summarize this project",
            session_id="session-123",
        )
    )

    assert args == [
        str(CLAUDE_PATH),
        "-p",
        "--output-format",
        "json",
        "--resume",
        "session-123",
        "Summarize this project",
    ]


def test_build_stream_command_without_resume() -> None:
    runner = ClaudeCodeCliRunner(CLAUDE_PATH)

    args = runner._build_stream_command(
        ClaudeCodeExecutionRequest(prompt="Explain recursion")
    )

    assert args == [
        str(CLAUDE_PATH),
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


def test_build_stream_command_forwards_claude_cli_options() -> None:
    runner = ClaudeCodeCliRunner(CLAUDE_PATH)

    args = runner._build_stream_command(
        ClaudeCodeExecutionRequest(
            prompt="Fix the build",
            session_id="session-123",
            permission_mode="acceptEdits",
            model="sonnet",
            allowed_tools=("Bash(git *)", "Edit"),
        )
    )

    assert args[-1] == "Fix the build"
    assert "--resume" in args and args[args.index("--resume") + 1] == "session-123"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--allowed-tools") + 1 :][:2] == ["Bash(git *)", "Edit"]


def test_extract_helpers_handle_supported_shapes() -> None:
    payload = {"result": "done", "session_id": "session-123"}
    stream_event = {
        "type": "stream_event",
        "event": {"delta": {"type": "text_delta", "text": "hello"}},
    }

    assert _extract_result_text(payload) == "done"
    assert _extract_session_id(payload) == "session-123"
    assert _extract_text_delta(stream_event) == "hello"


def test_extract_progress_surfaces_tool_use_activity() -> None:
    assistant_event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "let me look"},
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "git status\ngit diff"},
                },
            ]
        },
    }

    progress = _extract_progress(assistant_event)

    assert progress == [("\n· Bash(git status)\n", "message.tool_use")]


@pytest.mark.asyncio
async def test_stream_emits_deltas_incrementally_then_the_result(
    tmp_path: Path,
) -> None:
    events = [
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": "Hello"}},
        },
        {
            "type": "stream_event",
            "event": {"delta": {"type": "text_delta", "text": " world"}},
        },
        {"type": "result", "result": "Hello world", "session_id": "session-abc"},
    ]
    runner = ClaudeCodeCliRunner(_fake_cli(tmp_path, events))

    chunks = [
        chunk
        async for chunk in runner.stream(
            ClaudeCodeExecutionRequest(prompt="hi", timeout_seconds=30)
        )
    ]

    assert chunks[:2] == [
        StreamDeltaEvent(sequence=1, delta="Hello", snapshot="Hello"),
        StreamDeltaEvent(sequence=2, delta=" world", snapshot="Hello world"),
    ]
    result = chunks[-1]
    assert isinstance(result, ClaudeCodeExecutionResult)
    assert result.text == "Hello world"
    assert result.session_id == "session-abc"


@pytest.mark.asyncio
async def test_stream_fails_when_the_cli_never_emits_a_result(tmp_path: Path) -> None:
    runner = ClaudeCodeCliRunner(_fake_cli(tmp_path, [{"type": "system"}]))

    with pytest.raises(ClaudeCodeCliError):
        async for _ in runner.stream(
            ClaudeCodeExecutionRequest(prompt="hi", timeout_seconds=30)
        ):
            pass


def _fake_cli(tmp_path: Path, events: list[Any]) -> Path:
    """A stand-in `claude` executable that replays stream-json lines."""
    lines = [json.dumps(event) for event in events]
    script = tmp_path / "fake_claude.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import sys
            import time

            for line in {lines!r}:
                sys.stdout.write(line + "\\n")
                sys.stdout.flush()
                time.sleep(0.01)
            """
        ),
        encoding="utf-8",
    )

    shim = tmp_path / ("claude.cmd" if sys.platform == "win32" else "claude.sh")
    if sys.platform == "win32":
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}"\r\n')
    else:
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n')
        shim.chmod(0o755)
    return shim
