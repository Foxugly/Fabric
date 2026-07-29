from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
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


class ClaudeCodeLocalProvider(Provider):
    """Future provider for a user-owned local Claude Code session."""

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
        return list(self._capabilities.capabilities)

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "session.status":
            return await self.get_status()
        if action != "message.send":
            raise NotImplementedError(f"Unsupported claude_code_local action: {action}")

        cache_key = self._cache_key(action, payload)
        session_lock = self._lock_for_payload(payload)
        async with session_lock:
            cached_result = self._result_cache.pop(cache_key, None)
            if cached_result is not None:
                return cached_result

            result = await self._message_executor.execute(payload)
            return build_result_payload(result)

    def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if action != "message.send":
            raise NotImplementedError(f"Unsupported claude_code_local action: {action}")
        return self._stream_message_send(payload)

    async def _stream_message_send(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        session_lock = self._lock_for_payload(payload)
        async with session_lock:
            deltas, result = await self._message_executor.stream(payload)
            for delta in deltas:
                yield delta.to_progress_event()
            self._result_cache[self._cache_key("message.send", payload)] = (
                build_result_payload(result)
            )

    def _cache_key(self, action: str, payload: dict[str, Any]) -> str:
        canonical_payload = json.dumps(payload, sort_keys=True)
        return f"{action}:{canonical_payload}"

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
