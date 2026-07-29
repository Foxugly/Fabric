from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from fabric_agent.domain.provider import Provider
from fabric_agent.providers.windows_powershell.runner import (
    PowerShellExecutionError,
    PowerShellProbe,
    PowerShellProbeLike,
    PowerShellRunner,
)
from fabric_agent.providers.windows_powershell.session import (
    PowerShellSessionManager,
    PowerShellSessionManagerLike,
)

SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.*\-]{1,64}$")


class WindowsPowerShellProvider(Provider):
    """Safe Windows PowerShell adapter with explicit whitelisted actions only."""

    name = "windows_powershell"
    _capabilities = (
        "windows_powershell.system.info",
        "windows_powershell.process.list",
        "windows_powershell.claude.version",
        "windows_powershell.session.create",
        "windows_powershell.session.status",
        "windows_powershell.session.close",
        "windows_powershell.command.run",
        "windows_powershell.network.recover",
    )

    def __init__(
        self,
        probe: PowerShellProbeLike | None = None,
        session_manager: PowerShellSessionManagerLike | None = None,
    ) -> None:
        self._probe = probe or PowerShellProbe()
        self._session_manager = session_manager or PowerShellSessionManager(self._probe)
        self._stream_results: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get_status(self) -> dict[str, Any]:
        probe_result = self._probe.probe()
        return {
            "provider": self.name,
            "available": probe_result.available,
            "executable_path": (
                str(probe_result.executable_path)
                if probe_result.executable_path is not None
                else None
            ),
        }

    async def get_capabilities(self) -> list[str]:
        return list(self._capabilities)

    async def execute(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if action == "command.run":
            stream_key = _stream_key(action, payload)
            cached_result = self._stream_results.pop(stream_key, None)
            if cached_result is not None:
                return cached_result

        if action == "system.info":
            return await self._run_system_info(payload)
        if action == "process.list":
            return await self._run_process_list(payload)
        if action == "claude.version":
            return await self._run_claude_version(payload)
        if action == "session.create":
            return await self._run_session_create(payload)
        if action == "session.status":
            return await self._run_session_status(payload)
        if action == "session.close":
            return await self._run_session_close(payload)
        if action == "command.run":
            return await self._run_command(payload)
        if action == "network.recover":
            return await self._run_network_recover(payload)
        raise NotImplementedError(f"Unsupported windows_powershell action: {action}")

    async def cancel(self, action: str, payload: dict[str, Any]) -> None:
        if action != "command.run":
            raise NotImplementedError
        await self._session_manager.cancel_running_command(
            _required_session_id(payload)
        )

    async def stream(
        self,
        action: str,
        payload: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        command = payload.get("command")
        if action != "command.run":
            raise NotImplementedError
        if not isinstance(command, str) or not command.strip():
            raise NotImplementedError

        stream_key = _stream_key(action, payload)
        async for event in self._session_manager.stream_raw_command(
            _required_session_id(payload),
            command,
            timeout_seconds=_coerce_timeout(payload),
        ):
            if "_fabric_final_result" in event:
                self._stream_results[stream_key] = {
                    "provider": "windows_powershell",
                    "session_id": _required_session_id(payload),
                    "command": command,
                    "result": event["_fabric_final_result"],
                }
                continue
            yield event

    async def _run_system_info(self, payload: dict[str, Any]) -> dict[str, Any]:
        runner = self._runner()
        timeout = _coerce_timeout(payload)
        return await runner.run_json(
            (
                "$os = Get-CimInstance Win32_OperatingSystem; "
                "$cs = Get-CimInstance Win32_ComputerSystem; "
                "[pscustomobject]@{"
                "provider='windows_powershell';"
                "computer_name=$env:COMPUTERNAME;"
                "os_caption=$os.Caption;"
                "os_version=$os.Version;"
                "total_memory_bytes=[int64]$cs.TotalPhysicalMemory"
                "} | ConvertTo-Json -Compress"
            ),
            timeout_seconds=timeout,
        )

    async def _run_process_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        name_like = payload.get("name_like")
        if name_like is None:
            safe_filter = "*"
        elif isinstance(name_like, str) and SAFE_NAME_PATTERN.fullmatch(name_like):
            safe_filter = name_like
        else:
            raise PowerShellExecutionError(
                "name_like must match the safe pattern [A-Za-z0-9_.*-]{1,64}"
            )

        timeout = _coerce_timeout(payload)
        runner = self._runner()
        return await runner.run_json(
            (
                "$items = Get-Process | Where-Object { $_.ProcessName -like "
                f"'{safe_filter}' }} | "
                "Sort-Object ProcessName | "
                "Select-Object -First 25 ProcessName, Id, CPU, WS; "
                "[pscustomobject]@{"
                "provider='windows_powershell';"
                "processes=@($items | ForEach-Object { [pscustomobject]@{"
                "name=$_.ProcessName;"
                "pid=$_.Id;"
                "cpu=$_.CPU;"
                "working_set=$_.WS"
                "} })"
                "} | ConvertTo-Json -Compress -Depth 4"
            ),
            timeout_seconds=timeout,
        )

    async def _run_claude_version(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = _coerce_timeout(payload)
        runner = self._runner()
        return await runner.run_json(
            (
                "$claude = Get-Command claude -ErrorAction SilentlyContinue; "
                "if ($null -eq $claude) { "
                "  [pscustomobject]@{"
                "provider='windows_powershell';"
                "available=$false;"
                "version=$null"
                "}"
                "} else { "
                "  $version = (& claude --version 2>$null); "
                "  [pscustomobject]@{"
                "provider='windows_powershell';"
                "available=$true;"
                "version=$version"
                "}"
                "} | ConvertTo-Json -Compress"
            ),
            timeout_seconds=timeout,
        )

    def _runner(self) -> PowerShellRunner:
        probe_result = self._probe.probe()
        if probe_result.executable_path is None:
            raise PowerShellExecutionError("No PowerShell executable is available")
        return PowerShellRunner(probe_result.executable_path)

    async def _run_session_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._session_manager.create_session(
            working_directory=_optional_string(payload.get("working_directory")),
            timeout_seconds=_coerce_timeout(payload),
        )

    async def _run_session_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._session_manager.get_session_status(
            _required_session_id(payload)
        )

    async def _run_session_close(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._session_manager.close_session(_required_session_id(payload))

    async def _run_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = payload.get("command")
        if isinstance(command, str) and command.strip():
            return await self._session_manager.run_raw_command(
                _required_session_id(payload),
                command,
                timeout_seconds=_coerce_timeout(payload),
            )

        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation.strip():
            raise PowerShellExecutionError(
                "command is required for command.run"
            )
        return await self._session_manager.run_command(
            _required_session_id(payload),
            operation,
            payload,
            timeout_seconds=_coerce_timeout(payload),
        )

    async def _run_network_recover(self, payload: dict[str, Any]) -> dict[str, Any]:
        runner = self._runner()
        timeout = _coerce_timeout(payload)
        adapter_name = payload.get("adapter_name")
        if adapter_name is not None and (
            not isinstance(adapter_name, str) or not adapter_name.strip()
        ):
            raise PowerShellExecutionError("adapter_name must be a non-empty string")
        script = "ipconfig /flushdns | Out-Null; ipconfig /renew | Out-Null; "
        if adapter_name:
            safe_name = "'" + adapter_name.replace("'", "''") + "'"
            script += (
                f"Disable-NetAdapter -Name {safe_name} -Confirm:$false; "
                "Start-Sleep -Seconds 3; "
                f"Enable-NetAdapter -Name {safe_name} -Confirm:$false; "
            )
        script += (
            "[pscustomobject]@{provider='windows_powershell'; recovered=$true}"
            " | ConvertTo-Json -Compress"
        )
        return await runner.run_json(script, timeout_seconds=timeout)


def _coerce_timeout(payload: dict[str, Any]) -> int:
    timeout = payload.get("timeout_seconds", 30)
    if not isinstance(timeout, int) or timeout <= 0 or timeout > 120:
        raise PowerShellExecutionError(
            "timeout_seconds must be an integer between 1 and 120"
        )
    return timeout


def _required_session_id(payload: dict[str, Any]) -> str:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise PowerShellExecutionError("session_id is required")
    return session_id


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PowerShellExecutionError("working_directory must be a non-empty string")
    return value


def _stream_key(action: str, payload: dict[str, Any]) -> str:
    return json.dumps({"action": action, "payload": payload}, sort_keys=True)
