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
    PermissionPrompt,
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
        "--",
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
        "--",
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
    runner = _fake_cli(tmp_path, events)

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
    runner = _fake_cli(tmp_path, [{"type": "system"}])

    with pytest.raises(ClaudeCodeCliError):
        async for _ in runner.stream(
            ClaudeCodeExecutionRequest(prompt="hi", timeout_seconds=30)
        ):
            pass


def test_a_prompt_starting_with_a_dash_stays_a_prompt() -> None:
    runner = ClaudeCodeCliRunner(CLAUDE_PATH)

    args = runner._build_json_command(
        ClaudeCodeExecutionRequest(
            prompt="--dangerously-skip-permissions do it",
            allowed_tools=("Read",),
        )
    )

    assert args[-2:] == ["--", "--dangerously-skip-permissions do it"]
    assert args.index("--") > args.index("--allowed-tools")


@pytest.mark.parametrize("name", ["claude.cmd", "CLAUDE.CMD", "claude.bat"])
def test_batch_launchers_are_refused(name: str) -> None:
    with pytest.raises(ClaudeCodeCliError, match="cmd.exe"):
        ClaudeCodeCliRunner(Path("C:/npm") / name)


@pytest.mark.asyncio
async def test_gateway_token_never_reaches_the_command_line(tmp_path: Path) -> None:
    argv_log = tmp_path / "argv.json"
    runner = _fake_cli(
        tmp_path,
        [{"type": "result", "result": "ok", "session_id": "s"}],
        argv_log=argv_log,
    )
    secret = "gateway-secret-token"
    request = ClaudeCodeExecutionRequest(
        prompt="hi",
        timeout_seconds=30,
        permission_prompt=PermissionPrompt(port=1234, token=secret, command_id="c1"),
    )

    chunks = [chunk async for chunk in runner.stream(request)]

    assert isinstance(chunks[-1], ClaudeCodeExecutionResult)
    seen = json.loads(argv_log.read_text())
    assert all(secret not in arg for arg in seen["argv"])
    config_path = Path(seen["argv"][seen["argv"].index("--mcp-config") + 1])
    # The CLI read the token from the private file, which is gone afterwards.
    assert secret in seen["mcp_config"]
    assert not config_path.exists()


class _ScriptRunner(ClaudeCodeCliRunner):
    """Runs a Python stand-in for `claude` with the exact argv Fabric builds."""

    def __init__(self, script: Path) -> None:
        super().__init__(CLAUDE_PATH)
        self._script = script

    def _build_stream_command(
        self,
        request: ClaudeCodeExecutionRequest,
        mcp_config: Path | None = None,
    ) -> list[str]:
        args = super()._build_stream_command(request, mcp_config)
        return [sys.executable, str(self._script), *args[1:]]


def _fake_cli(
    tmp_path: Path, events: list[Any], argv_log: Path | None = None
) -> ClaudeCodeCliRunner:
    """A stand-in `claude` that replays stream-json lines.

    With `argv_log`, it also records its argv and the content of the file passed
    to `--mcp-config`, as seen while it runs.
    """
    lines = [json.dumps(event) for event in events]
    script = tmp_path / "fake_claude.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import json
            import sys
            import time

            argv_log = {str(argv_log) if argv_log else None!r}
            if argv_log:
                args = sys.argv[1:]
                config = None
                if "--mcp-config" in args:
                    with open(args[args.index("--mcp-config") + 1]) as handle:
                        config = handle.read()
                with open(argv_log, "w") as handle:
                    json.dump({{"argv": args, "mcp_config": config}}, handle)

            for line in {lines!r}:
                sys.stdout.write(line + "\\n")
                sys.stdout.flush()
                time.sleep(0.01)
            """
        ),
        encoding="utf-8",
    )
    return _ScriptRunner(script)
