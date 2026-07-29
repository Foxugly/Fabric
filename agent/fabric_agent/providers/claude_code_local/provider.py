from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from fabric_agent.domain.provider import Provider
from fabric_agent.providers.claude_code_local.detector import (
    ClaudeCodeSessionStatusDetector,
)
from fabric_agent.providers.claude_code_local.executor import (
    ClaudeCodeMessageExecutor,
    build_result_payload,
)
from fabric_agent.providers.claude_code_local.models import (
    ClaudeCodeLocalCapabilities,
)
from fabric_agent.providers.claude_code_local.runner import ClaudeCodeExecutionResult


class ClaudeCodeLocalProvider(Provider):
    """Drives a user-owned local Claude Code session through the CLI."""

    name = "claude_code_local"

    def __init__(
        self,
        status_detector: ClaudeCodeSessionStatusDetector | None = None,
        message_executor: ClaudeCodeMessageExecutor | None = None,
    ) -> None:
        self._capabilities = ClaudeCodeLocalCapabilities()
        self._status_detector = status_detector or ClaudeCodeSessionStatusDetector()
        self._message_executor = message_executor or ClaudeCodeMessageExecutor()
        self._result_cache: dict[str, dict[str, Any]] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_status(self) -> dict[str, Any]:
        return self._status_detector.detect().to_dict()

    async def get_capabilities(self) -> list[str]:
        return list(self._capabilities.qualified_capabilities())

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "session.status":
            return await self.get_status()
        if action != "message.send":
            raise NotImplementedError(f"Unsupported claude_code_local action: {action}")

        cached_result = self._result_cache.pop(self._cache_key(payload), None)
        if cached_result is not None:
            return cached_result

        async with self._lock_for_payload(payload):
            result = await self._message_executor.execute(payload)
            return build_result_payload(result)

    async def cancel(self, action: str, payload: dict[str, Any]) -> None:
        if action != "message.send":
            raise NotImplementedError(f"Unsupported claude_code_local action: {action}")
        self._result_cache.pop(self._cache_key(payload), None)
        await self._message_executor.cancel(payload)

    def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        if action != "message.send":
            raise NotImplementedError(f"Unsupported claude_code_local action: {action}")
        return self._stream_message_send(payload)

    async def _stream_message_send(
        self,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with self._lock_for_payload(payload):
            async for chunk in self._message_executor.stream(payload):
                if isinstance(chunk, ClaudeCodeExecutionResult):
                    self._result_cache[self._cache_key(payload)] = build_result_payload(
                        chunk
                    )
                    continue
                yield chunk.to_progress_event()

    def _cache_key(self, payload: dict[str, Any]) -> str:
        command_id = payload.get("_fabric_command_id")
        if isinstance(command_id, str) and command_id:
            return f"command:{command_id}"
        return f"session:{self._session_key(payload)}"

    def _lock_for_payload(self, payload: dict[str, Any]) -> asyncio.Lock:
        session_key = self._session_key(payload)
        lock = self._session_locks.get(session_key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_key] = lock
        return lock

    def _session_key(self, payload: dict[str, Any]) -> str:
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            return f"session:{session_id}"
        working_directory = payload.get("working_directory")
        if isinstance(working_directory, str) and working_directory.strip():
            return f"cwd:{working_directory}"
        return "session:default"
