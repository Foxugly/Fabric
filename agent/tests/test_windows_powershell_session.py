from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from fabric_agent.providers.windows_powershell.provider import WindowsPowerShellProvider
from fabric_agent.providers.windows_powershell.runner import (
    PowerShellExecutionError,
    PowerShellProbeResult,
)
from fabric_agent.providers.windows_powershell.session import (
    PowerShellSessionManagerLike,
)


class StubProbe:
    def __init__(self, executable_path: Path | None) -> None:
        self._result = PowerShellProbeResult(executable_path)

    def probe(self) -> PowerShellProbeResult:
        return self._result


class FakeSessionManager(PowerShellSessionManagerLike):
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}

    async def create_session(
        self,
        *,
        working_directory: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        session = {
            "session_id": "session-1",
            "working_directory": working_directory or "C:\\work",
            "created_at": "2026-07-29T10:00:00+00:00",
            "last_used_at": "2026-07-29T10:00:00+00:00",
            "available": True,
            "busy": False,
        }
        self.sessions["session-1"] = session
        return {"provider": "windows_powershell", "session": session}

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        return {"provider": "windows_powershell", "session": self.sessions[session_id]}

    async def close_session(self, session_id: str) -> dict[str, Any]:
        self.sessions.pop(session_id, None)
        return {
            "provider": "windows_powershell",
            "session_id": session_id,
            "closed": True,
        }

    async def run_command(
        self,
        session_id: str,
        operation: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        if operation == "set_location":
            self.sessions[session_id]["working_directory"] = str(payload["path"])
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "operation": operation,
                "result": {"path": str(payload["path"])},
            }
        if operation == "get_location":
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "operation": operation,
                "result": {"path": self.sessions[session_id]["working_directory"]},
            }
        if operation == "get_network_status":
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "operation": operation,
                "result": {
                    "computer_name": "PC-1",
                    "adapters": [
                        {"interface_alias": "Ethernet", "ipv4": ["192.168.1.10"]}
                    ],
                },
            }
        if operation == "get_python_process_status":
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "operation": operation,
                "result": {"processes": [{"name": "python", "pid": 1234}]},
            }
        if operation == "get_file_summary":
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "operation": operation,
                "result": {"path": str(payload["path"]), "exists": True},
            }
        raise PowerShellExecutionError("unsupported operation")

    async def run_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        if command == "Get-Location":
            return {
                "provider": "windows_powershell",
                "session_id": session_id,
                "command": command,
                "result": {
                    "stdout": self.sessions[session_id]["working_directory"],
                    "stderr": "",
                    "exit_code": 0,
                    "success": True,
                    "path": self.sessions[session_id]["working_directory"],
                },
            }
        raise PowerShellExecutionError("unsupported raw command")

    async def _stream_raw_command_impl(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        del timeout_seconds
        if command != "Get-Location":
            raise PowerShellExecutionError("unsupported raw command")
        yield {
            "event": "terminal.stdout",
            "stream": "stdout",
            "sequence": 1,
            "delta": self.sessions[session_id]["working_directory"] + "\n",
        }
        yield {
            "_fabric_final_result": {
                "stdout": self.sessions[session_id]["working_directory"] + "\n",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "path": self.sessions[session_id]["working_directory"],
            }
        }

    def stream_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        return self._stream_raw_command_impl(
            session_id,
            command,
            timeout_seconds=timeout_seconds,
        )

    async def cancel_running_command(self, session_id: str) -> None:
        del session_id
        return None


@pytest.mark.asyncio
async def test_windows_powershell_persistent_session_lifecycle() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe")),
        session_manager=FakeSessionManager(),
    )

    created = await provider.execute("session.create",
        {"working_directory": "C:\\work"},
    )
    session_id = created["session"]["session_id"]

    await provider.execute("command.run",
        {
            "session_id": session_id,
            "operation": "set_location",
            "path": "C:\\other",
        },
    )
    location = await provider.execute("command.run",
        {"session_id": session_id, "operation": "get_location"},
    )
    status = await provider.execute("session.status",
        {"session_id": session_id},
    )
    closed = await provider.execute("session.close",
        {"session_id": session_id},
    )

    assert location["result"]["path"] == "C:\\other"
    assert status["session"]["working_directory"] == "C:\\other"
    assert closed["closed"] is True


@pytest.mark.asyncio
async def test_windows_powershell_persistent_session_diagnostic_operations() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe")),
        session_manager=FakeSessionManager(),
    )

    created = await provider.execute("session.create",
        {"working_directory": "C:\\work"},
    )
    session_id = created["session"]["session_id"]

    network_status = await provider.execute("command.run",
        {"session_id": session_id, "operation": "get_network_status"},
    )
    python_status = await provider.execute("command.run",
        {"session_id": session_id, "operation": "get_python_process_status"},
    )
    file_summary = await provider.execute("command.run",
        {
            "session_id": session_id,
            "operation": "get_file_summary",
            "path": "C:\\work\\notes.txt",
        },
    )

    assert network_status["result"]["adapters"][0]["interface_alias"] == "Ethernet"
    assert python_status["result"]["processes"][0]["name"] == "python"
    assert file_summary["result"]["exists"] is True


@pytest.mark.asyncio
async def test_windows_powershell_persistent_session_allows_terminal_command() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe")),
        session_manager=FakeSessionManager(),
    )

    created = await provider.execute("session.create",
        {"working_directory": "C:\\work"},
    )
    session_id = created["session"]["session_id"]

    result = await provider.execute("command.run",
        {"session_id": session_id, "command": "Get-Location"},
    )

    assert result["result"]["success"] is True
    assert result["result"]["stdout"] == "C:\\work"
