from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ClaudeCodeCliError(RuntimeError):
    """Raised when the Claude Code CLI returns an unusable result."""


@dataclass(slots=True, frozen=True)
class ClaudeCodeExecutionRequest:
    prompt: str
    session_id: str | None = None
    working_directory: Path | None = None
    timeout_seconds: int = 300


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

    def to_progress_event(self) -> dict[str, Any]:
        return {
            "event": "message.delta",
            "sequence": self.sequence,
            "delta": self.delta,
            "snapshot": self.snapshot,
        }


class ClaudeCodeCliRunner:
    def __init__(self, executable_path: Path) -> None:
        self._executable_path = executable_path

    async def execute(
        self,
        request: ClaudeCodeExecutionRequest,
    ) -> ClaudeCodeExecutionResult:
        args = self._build_json_command(request)
        completed = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=(
                    str(request.working_directory)
                    if request.working_directory
                    else None
                ),
            ),
            timeout=request.timeout_seconds,
        )
        stdout, stderr = await asyncio.wait_for(
            completed.communicate(),
            timeout=request.timeout_seconds,
        )

        if completed.returncode != 0:
            raise ClaudeCodeCliError(
                f"Claude Code CLI failed with exit code {completed.returncode}: "
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
    ) -> tuple[list[StreamDeltaEvent], ClaudeCodeExecutionResult]:
        args = self._build_stream_command(request)
        process = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=(
                    str(request.working_directory)
                    if request.working_directory
                    else None
                ),
            ),
            timeout=request.timeout_seconds,
        )

        deltas: list[StreamDeltaEvent] = []
        snapshot = ""
        result_payload: dict[str, Any] | None = None

        async def _read_stdout() -> None:
            nonlocal snapshot, result_payload
            if process.stdout is None:
                raise ClaudeCodeCliError("Claude Code CLI stream has no stdout pipe")
            while True:
                raw_line = await process.stdout.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "stream_event":
                    delta = _extract_text_delta(event)
                    if delta:
                        snapshot += delta
                        deltas.append(
                            StreamDeltaEvent(
                                sequence=len(deltas) + 1,
                                delta=delta,
                                snapshot=snapshot,
                            )
                        )
                elif event.get("type") == "result":
                    result_payload = event

        try:
            await asyncio.wait_for(_read_stdout(), timeout=request.timeout_seconds)
            if process.stderr is None:
                raise ClaudeCodeCliError("Claude Code CLI stream has no stderr pipe")
            stderr_output = await asyncio.wait_for(
                process.stderr.read(),
                timeout=request.timeout_seconds,
            )
            returncode = await asyncio.wait_for(
                process.wait(),
                timeout=request.timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            raise ClaudeCodeCliError("Claude Code CLI streaming timed out") from exc

        if returncode != 0:
            raise ClaudeCodeCliError(
                f"Claude Code CLI stream failed with exit code {returncode}: "
                f"{stderr_output.decode('utf-8', errors='replace').strip()}"
            )
        if result_payload is None:
            raise ClaudeCodeCliError(
                "Claude Code CLI stream ended without result event"
            )

        final_result = ClaudeCodeExecutionResult(
            text=_extract_result_text(result_payload),
            session_id=_extract_session_id(result_payload),
            raw_payload=result_payload,
        )
        return deltas, final_result

    def _build_json_command(self, request: ClaudeCodeExecutionRequest) -> list[str]:
        args = [
            str(self._executable_path),
            "-p",
            "--output-format",
            "json",
        ]
        if request.session_id:
            args.extend(["--resume", request.session_id])
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
        if request.session_id:
            args.extend(["--resume", request.session_id])
        args.append(request.prompt)
        return args


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
