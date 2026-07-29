from __future__ import annotations

from pathlib import Path
from typing import Any

from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeInstallationProbe,
    ClaudeCodeInstallationProbeLike,
)
from fabric_agent.providers.claude_code_local.runner import (
    ClaudeCodeCliError,
    ClaudeCodeCliRunner,
    ClaudeCodeExecutionRequest,
    ClaudeCodeExecutionResult,
    StreamDeltaEvent,
)


class ClaudeCodePayloadError(ValueError):
    """Raised when a claude_code_local payload is invalid."""


class ClaudeCodeMessageExecutor:
    def __init__(
        self,
        installation_probe: ClaudeCodeInstallationProbeLike | None = None,
    ) -> None:
        self._installation_probe = installation_probe or ClaudeCodeInstallationProbe()

    async def execute(self, payload: dict[str, Any]) -> ClaudeCodeExecutionResult:
        request = self._build_request(payload)
        runner = self._runner()
        return await runner.execute(request)

    async def stream(
        self,
        payload: dict[str, Any],
    ) -> tuple[list[StreamDeltaEvent], ClaudeCodeExecutionResult]:
        request = self._build_request(payload)
        runner = self._runner()
        return await runner.stream(request)

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

        session_id = payload.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise ClaudeCodePayloadError("session_id must be a string when provided")

        working_directory = payload.get("working_directory")
        resolved_directory: Path | None = None
        if working_directory is not None:
            if not isinstance(working_directory, str) or not working_directory.strip():
                raise ClaudeCodePayloadError(
                    "working_directory must be a non-empty string when provided"
                )
            resolved_directory = Path(working_directory).expanduser()

        timeout_seconds = payload.get("timeout_seconds", 300)
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise ClaudeCodePayloadError("timeout_seconds must be a positive integer")

        return ClaudeCodeExecutionRequest(
            prompt=prompt,
            session_id=session_id,
            working_directory=resolved_directory,
            timeout_seconds=timeout_seconds,
        )


def build_result_payload(result: ClaudeCodeExecutionResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "session_id": result.session_id,
        "raw": _sanitize_raw_payload(result.raw_payload),
    }


def _sanitize_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in ("session_id", "total_cost_usd", "duration_ms", "usage"):
        value = payload.get(key)
        if value is not None:
            sanitized[key] = value
    return sanitized
