from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Claude Code emits one JSON document per line in `stream-json` mode. A single
# line can carry a full tool result, which easily exceeds the 64 KiB default
# limit of `asyncio.StreamReader`.
STREAM_LINE_LIMIT = 8 * 1024 * 1024

PERMISSION_MODES = frozenset(
    {"acceptEdits", "auto", "bypassPermissions", "manual", "plan", "default"}
)


class ClaudeCodeCliError(RuntimeError):
    """Raised when the Claude Code CLI returns an unusable result."""


ProcessObserver = Callable[[asyncio.subprocess.Process], None]


@dataclass(slots=True, frozen=True)
class ClaudeCodeExecutionRequest:
    prompt: str
    session_id: str | None = None
    working_directory: Path | None = None
    timeout_seconds: int = 300
    permission_mode: str | None = None
    model: str | None = None
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    disallowed_tools: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True, frozen=True)
class ClaudeCodeExecutionResult:
    text: str
    session_id: str | None
    raw_payload: dict[str, Any]


@dataclass(slots=True, frozen=True)
class StreamDeltaEvent:
    sequence: int
    delta: str
    snapshot: str
    event: str = "message.delta"

    def to_progress_event(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "sequence": self.sequence,
            "delta": self.delta,
            "snapshot": self.snapshot,
        }


StreamChunk = StreamDeltaEvent | ClaudeCodeExecutionResult


class ClaudeCodeCliRunner:
    def __init__(self, executable_path: Path) -> None:
        self._executable_path = executable_path

    async def execute(
        self,
        request: ClaudeCodeExecutionRequest,
        *,
        on_start: ProcessObserver | None = None,
    ) -> ClaudeCodeExecutionResult:
        process = await self._spawn(self._build_json_command(request), request)
        if on_start is not None:
            on_start(process)

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=request.timeout_seconds,
            )
        except TimeoutError as exc:
            await _terminate(process)
            raise ClaudeCodeCliError("Claude Code CLI timed out") from exc

        if process.returncode != 0:
            raise ClaudeCodeCliError(
                f"Claude Code CLI failed with exit code {process.returncode}: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )

        payload = _parse_json_payload(
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
        return ClaudeCodeExecutionResult(
            text=_extract_result_text(payload),
            session_id=_extract_session_id(payload),
            raw_payload=payload,
        )

    async def stream(
        self,
        request: ClaudeCodeExecutionRequest,
        *,
        on_start: ProcessObserver | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Yield deltas as the CLI produces them, then the final result."""
        process = await self._spawn(self._build_stream_command(request), request)
        if on_start is not None:
            on_start(process)

        if process.stdout is None or process.stderr is None:
            await _terminate(process)
            raise ClaudeCodeCliError("Claude Code CLI stream has no output pipes")

        # stderr must be drained concurrently: a full stderr pipe blocks the CLI
        # and would deadlock a stdout-only reader.
        stderr_task = asyncio.create_task(process.stderr.read())
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        snapshot = ""
        sequence = 0
        result_payload: dict[str, Any] | None = None

        try:
            while True:
                line = await _read_line(process.stdout, deadline)
                if line is None:
                    break
                event = _decode_stream_line(line)
                if event is None:
                    continue

                if event.get("type") == "result":
                    result_payload = event
                    continue

                for delta, kind in _extract_progress(event):
                    snapshot += delta
                    sequence += 1
                    yield StreamDeltaEvent(
                        sequence=sequence,
                        delta=delta,
                        snapshot=snapshot,
                        event=kind,
                    )

            returncode = await _await_with_deadline(process.wait(), deadline)
            stderr_output = (await stderr_task).decode("utf-8", errors="replace")
        except TimeoutError as exc:
            raise ClaudeCodeCliError("Claude Code CLI streaming timed out") from exc
        finally:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
            await _terminate(process)

        if returncode != 0:
            raise ClaudeCodeCliError(
                f"Claude Code CLI stream failed with exit code {returncode}: "
                f"{stderr_output.strip()}"
            )
        if result_payload is None:
            raise ClaudeCodeCliError(
                "Claude Code CLI stream ended without result event"
            )

        yield ClaudeCodeExecutionResult(
            text=_extract_result_text(result_payload),
            session_id=_extract_session_id(result_payload),
            raw_payload=result_payload,
        )

    async def _spawn(
        self,
        args: list[str],
        request: ClaudeCodeExecutionRequest,
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LINE_LIMIT,
            cwd=(
                str(request.working_directory) if request.working_directory else None
            ),
        )

    def _build_json_command(self, request: ClaudeCodeExecutionRequest) -> list[str]:
        args = [
            str(self._executable_path),
            "-p",
            "--output-format",
            "json",
        ]
        args.extend(self._build_session_args(request))
        args.append(request.prompt)
        return args

    def _build_stream_command(self, request: ClaudeCodeExecutionRequest) -> list[str]:
        args = [
            str(self._executable_path),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        args.extend(self._build_session_args(request))
        args.append(request.prompt)
        return args

    def _build_session_args(self, request: ClaudeCodeExecutionRequest) -> list[str]:
        args: list[str] = []
        if request.session_id:
            args.extend(["--resume", request.session_id])
        if request.permission_mode:
            args.extend(["--permission-mode", request.permission_mode])
        if request.model:
            args.extend(["--model", request.model])
        if request.allowed_tools:
            args.extend(["--allowed-tools", *request.allowed_tools])
        if request.disallowed_tools:
            args.extend(["--disallowed-tools", *request.disallowed_tools])
        return args


async def _read_line(reader: asyncio.StreamReader, deadline: float) -> bytes | None:
    remaining = max(0.1, deadline - asyncio.get_running_loop().time())
    try:
        raw_line = await asyncio.wait_for(reader.readline(), timeout=remaining)
    except ValueError as exc:
        # A single JSON line exceeded STREAM_LINE_LIMIT.
        raise ClaudeCodeCliError(
            "Claude Code CLI emitted a stream line above the supported size limit"
        ) from exc
    return raw_line or None


async def _await_with_deadline(awaitable: Any, deadline: float) -> Any:
    remaining = max(0.1, deadline - asyncio.get_running_loop().time())
    return await asyncio.wait_for(awaitable, timeout=remaining)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.wait()


def _decode_stream_line(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        # The CLI can interleave non-JSON diagnostics; skipping is safer than
        # failing a turn that is otherwise healthy.
        return None
    return event if isinstance(event, dict) else None


def _parse_json_payload(stdout: str, stderr: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCodeCliError(
            f"Claude Code CLI returned invalid JSON. stderr={stderr.strip()}"
        ) from exc
    if not isinstance(payload, dict):
        raise ClaudeCodeCliError("Claude Code CLI JSON payload must be an object")
    return payload


def _extract_result_text(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if isinstance(result, str):
        return result
    raise ClaudeCodeCliError("Claude Code CLI payload does not contain a text result")


def _extract_session_id(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id")
    return session_id if isinstance(session_id, str) else None


def _extract_progress(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Turn one stream-json event into renderable (delta, event_type) pairs."""
    if event.get("type") == "stream_event":
        delta = _extract_text_delta(event)
        return [(delta, "message.delta")] if delta else []
    if event.get("type") == "assistant":
        return [
            (summary, "message.tool_use")
            for summary in _extract_tool_use_summaries(event)
        ]
    return []


def _extract_text_delta(event: dict[str, Any]) -> str:
    event_payload = event.get("event")
    if not isinstance(event_payload, dict):
        return ""
    delta = event_payload.get("delta")
    if not isinstance(delta, dict):
        return ""
    if delta.get("type") != "text_delta":
        return ""
    text = delta.get("text")
    return text if isinstance(text, str) else ""


def _extract_tool_use_summaries(event: dict[str, Any]) -> list[str]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []

    summaries: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str):
            continue
        summaries.append(f"\n· {name}{_format_tool_hint(block.get('input'))}\n")
    return summaries


def _format_tool_hint(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "pattern", "path", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            hint = value.strip().splitlines()[0]
            return f"({hint[:120]})"
    return ""
