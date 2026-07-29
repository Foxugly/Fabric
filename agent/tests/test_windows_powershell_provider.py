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


class FakeProvider(WindowsPowerShellProvider):
    def __init__(self) -> None:
        super().__init__(probe=StubProbe(Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")))
        self.scripts: list[str] = []

    async def _run_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"provider": self.name, "computer_name": "PC-1"}

    async def _run_process_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"provider": self.name, "processes": [{"name": "python", "pid": 1234}]}

    async def _run_claude_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"provider": self.name, "available": True, "version": "claude 1.2.3"}


class FakeSessionManager(PowerShellSessionManagerLike):
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {
            "session-1": {
                "session_id": "session-1",
                "working_directory": "C:\\work",
                "created_at": "2026-07-29T10:00:00+00:00",
                "last_used_at": "2026-07-29T10:00:00+00:00",
                "available": True,
                "busy": False,
            }
        }

    async def create_session(
        self,
        *,
        working_directory: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        session = dict(self.sessions["session-1"])
        if working_directory is not None:
            session["working_directory"] = working_directory
            self.sessions["session-1"]["working_directory"] = working_directory
        return {"provider": "windows_powershell", "session": session}

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        return {"provider": "windows_powershell", "session": self.sessions[session_id]}

    async def close_session(self, session_id: str) -> dict[str, Any]:
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
        del payload, timeout_seconds
        return {
            "provider": "windows_powershell",
            "session_id": session_id,
            "operation": operation,
            "result": {"path": self.sessions[session_id]["working_directory"]},
        }

    async def run_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        working_directory = str(self.sessions[session_id]["working_directory"])
        return {
            "provider": "windows_powershell",
            "session_id": session_id,
            "command": command,
            "result": {
                "stdout": working_directory + "\n",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "path": working_directory,
            },
        }

    async def _stream_raw_command_impl(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        del command, timeout_seconds
        working_directory = str(self.sessions[session_id]["working_directory"])
        yield {
            "event": "terminal.stdout",
            "stream": "stdout",
            "sequence": 1,
            "delta": working_directory + "\n",
        }
        yield {
            "_fabric_final_result": {
                "stdout": working_directory + "\n",
                "stderr": "",
                "exit_code": 0,
                "success": True,
                "path": working_directory,
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
async def test_windows_powershell_status_reports_availability() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe"))
    )

    status = await provider.get_status()

    assert status["provider"] == "windows_powershell"
    assert status["available"] is True


@pytest.mark.asyncio
async def test_windows_powershell_executes_whitelisted_actions() -> None:
    provider = FakeProvider()

    system_info = await provider.execute("system.info", {})
    process_list = await provider.execute("process.list",
        {"name_like": "python*"},
    )
    claude_version = await provider.execute("claude.version", {})

    assert system_info["computer_name"] == "PC-1"
    assert process_list["processes"][0]["name"] == "python"
    assert claude_version["available"] is True
    assert "windows_powershell.command.run" in await provider.get_capabilities()
    assert "windows_powershell.network.recover" in await provider.get_capabilities()


@pytest.mark.asyncio
async def test_windows_powershell_rejects_unknown_action() -> None:
    provider = FakeProvider()

    with pytest.raises(NotImplementedError):
        await provider.execute("shell.run", {})


@pytest.mark.asyncio
async def test_windows_powershell_rejects_unsafe_name_filter() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe"))
    )

    with pytest.raises(PowerShellExecutionError):
        await provider.execute("process.list",
            {"name_like": "python; Remove-Item C:\\"},
        )


@pytest.mark.asyncio
async def test_windows_powershell_command_stream_caches_final_result() -> None:
    provider = WindowsPowerShellProvider(
        probe=StubProbe(Path("C:/Windows/powershell.exe")),
        session_manager=FakeSessionManager(),
    )

    await provider.execute("session.create",
        {"working_directory": "C:\\work"},
    )

    progress_events = [
        event
        async for event in provider.stream("command.run",
            {"session_id": "session-1", "command": "Get-Location"},
        )
    ]
    result = await provider.execute("command.run",
        {"session_id": "session-1", "command": "Get-Location"},
    )

    assert progress_events[0]["event"] == "terminal.stdout"
    assert result["result"]["success"] is True
    assert result["result"]["stdout"] == "C:\\work\n"
