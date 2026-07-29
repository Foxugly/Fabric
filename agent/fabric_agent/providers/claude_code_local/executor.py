from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fabric_agent.permissions.gateway import PermissionGateway
from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeInstallationProbe,
    ClaudeCodeInstallationProbeLike,
)
from fabric_agent.providers.claude_code_local.runner import (
    PERMISSION_MODES,
    ClaudeCodeCliError,
    ClaudeCodeCliRunner,
    ClaudeCodeExecutionRequest,
    ClaudeCodeExecutionResult,
    PermissionPrompt,
    StreamChunk,
)

COMMAND_ID_KEY = "_fabric_command_id"
TIMEOUT_KEY = "_fabric_timeout_seconds"
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 3600


class ClaudeCodePayloadError(ValueError):
    """Raised when a claude_code_local payload is invalid."""


class ClaudeCodeMessageExecutor:
    def __init__(
        self,
        installation_probe: ClaudeCodeInstallationProbeLike | None = None,
        permission_gateway: PermissionGateway | None = None,
    ) -> None:
        self._installation_probe = installation_probe or ClaudeCodeInstallationProbe()
        self._permission_gateway = permission_gateway
        self._running: dict[str, asyncio.subprocess.Process] = {}

    async def execute(self, payload: dict[str, Any]) -> ClaudeCodeExecutionResult:
        request = self._build_request(payload)
        runner = self._runner()
        command_id = _command_id(payload)
        try:
            return await runner.execute(
                request,
                on_start=lambda process: self._track(command_id, process),
            )
        finally:
            self._running.pop(command_id, None)

    async def stream(
        self,
        payload: dict[str, Any],
    ) -> AsyncGenerator[StreamChunk, None]:
        request = self._build_request(payload)
        runner = self._runner()
        command_id = _command_id(payload)
        try:
            async for chunk in runner.stream(
                request,
                on_start=lambda process: self._track(command_id, process),
            ):
                yield chunk
        finally:
            self._running.pop(command_id, None)

    async def cancel(self, payload: dict[str, Any]) -> None:
        process = self._running.pop(_command_id(payload), None)
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(ProcessLookupError):
            await process.wait()

    def _track(self, command_id: str, process: asyncio.subprocess.Process) -> None:
        self._running[command_id] = process

    def _runner(self) -> ClaudeCodeCliRunner:
        installation = self._installation_probe.probe()
        if installation.executable_path is None:
            raise ClaudeCodeCliError(
                "Claude Code CLI executable is not available on this machine"
            )
        return ClaudeCodeCliRunner(installation.executable_path)

    def _build_request(
        self,
        payload: dict[str, Any],
    ) -> ClaudeCodeExecutionRequest:
        prompt = payload.get("text", payload.get("prompt"))
        if not isinstance(prompt, str) or not prompt.strip():
            raise ClaudeCodePayloadError(
                "claude_code_local.message.send requires a non-empty text payload"
            )

        return ClaudeCodeExecutionRequest(
            prompt=prompt,
            session_id=_optional_string(payload.get("session_id"), "session_id"),
            working_directory=_optional_directory(payload.get("working_directory")),
            timeout_seconds=_timeout_seconds(payload),
            permission_mode=_permission_mode(payload.get("permission_mode")),
            model=_optional_string(payload.get("model"), "model"),
            allowed_tools=_tool_list(payload.get("allowed_tools"), "allowed_tools"),
            disallowed_tools=_tool_list(
                payload.get("disallowed_tools"), "disallowed_tools"
            ),
            permission_prompt=self._permission_prompt(payload),
        )

    def _permission_prompt(self, payload: dict[str, Any]) -> PermissionPrompt | None:
        """Wire the approval bridge unless the caller opted out.

        Without it, `-p` denies every tool that needs approval, so the bridge is
        on by default; a payload can disable it to fall back on a blanket
        `permission_mode` such as `acceptEdits`.
        """
        if payload.get("permission_prompt") is False:
            return None
        gateway = self._permission_gateway
        if gateway is None or not gateway.is_running:
            return None
        return PermissionPrompt(
            port=gateway.port,
            token=gateway.token,
            command_id=_command_id(payload),
        )


def build_result_payload(result: ClaudeCodeExecutionResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "session_id": result.session_id,
        "raw": _sanitize_raw_payload(result.raw_payload),
    }


def _command_id(payload: dict[str, Any]) -> str:
    command_id = payload.get(COMMAND_ID_KEY)
    return command_id if isinstance(command_id, str) else "unknown"


def _optional_string(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ClaudeCodePayloadError(
            f"{field_name} must be a non-empty string when provided"
        )
    return value


def _optional_directory(value: Any) -> Path | None:
    working_directory = _optional_string(value, "working_directory")
    if working_directory is None:
        return None
    resolved = Path(working_directory).expanduser()
    if not resolved.is_dir():
        raise ClaudeCodePayloadError(
            "working_directory must point to an existing directory"
        )
    return resolved


def _timeout_seconds(payload: dict[str, Any]) -> int:
    timeout_seconds = payload.get(
        "timeout_seconds",
        payload.get(TIMEOUT_KEY, DEFAULT_TIMEOUT_SECONDS),
    )
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
    ):
        raise ClaudeCodePayloadError(
            f"timeout_seconds must be an integer between 1 and {MAX_TIMEOUT_SECONDS}"
        )
    return timeout_seconds


def _permission_mode(value: Any) -> str | None:
    permission_mode = _optional_string(value, "permission_mode")
    if permission_mode is None:
        return None
    if permission_mode not in PERMISSION_MODES:
        raise ClaudeCodePayloadError(
            "permission_mode must be one of " + ", ".join(sorted(PERMISSION_MODES))
        )
    return permission_mode


def _tool_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        raise ClaudeCodePayloadError(
            f"{field_name} must be a list of non-empty strings"
        )
    return tuple(value)


def _sanitize_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in ("session_id", "total_cost_usd", "duration_ms", "usage", "num_turns"):
        value = payload.get(key)
        if value is not None:
            sanitized[key] = value
    return sanitized
