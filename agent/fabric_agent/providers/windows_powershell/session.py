from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from fabric_agent.providers.windows_powershell.runner import (
    PowerShellExecutionError,
    PowerShellProbe,
    PowerShellProbeLike,
)


@dataclass(slots=True)
class PowerShellSessionState:
    session_id: str
    working_directory: str
    created_at: str
    last_used_at: str
    available: bool
    busy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "working_directory": self.working_directory,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "available": self.available,
            "busy": self.busy,
        }


class PowerShellSessionManagerLike(Protocol):
    async def create_session(
        self,
        *,
        working_directory: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ...

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        ...

    async def close_session(self, session_id: str) -> dict[str, Any]:
        ...

    async def run_command(
        self,
        session_id: str,
        operation: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ...

    async def run_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        ...

    def stream_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        ...

    async def cancel_running_command(self, session_id: str) -> None:
        ...


class PowerShellPersistentSession:
    def __init__(
        self,
        session_id: str,
        executable_path: Path,
        process: asyncio.subprocess.Process,
        working_directory: str,
    ) -> None:
        self.session_id = session_id
        self.executable_path = executable_path
        self.process = process
        self.working_directory = working_directory
        now = _utcnow_iso()
        self.created_at = now
        self.last_used_at = now
        self.lock = asyncio.Lock()
        self._cancel_requested = False

    @classmethod
    async def create(
        cls,
        *,
        executable_path: Path,
        working_directory: str,
    ) -> PowerShellPersistentSession:
        process = await _start_powershell_process(executable_path)
        session = cls(
            session_id=str(uuid4()),
            executable_path=executable_path,
            process=process,
            working_directory=working_directory,
        )
        await _set_process_location(process, working_directory)
        return session

    async def run_operation(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        script_body = _build_session_operation_script(operation, payload)
        marker = f"__FABRIC_PS__{uuid4()}__"
        script = (
            "$ErrorActionPreference = 'Stop'; "
            "try { "
            f"{script_body} "
            "$__fabric_payload = [pscustomobject]@{ok=$true;result=$__fabric_result} "
            "| ConvertTo-Json -Compress -Depth 6; "
            "} catch { "
            "$__fabric_payload = [pscustomobject]@{"
            "ok=$false;"
            "error=$_.Exception.Message"
            "} "
            "| ConvertTo-Json -Compress -Depth 6; "
            "} "
            f"Write-Output '{marker}'; "
            "Write-Output $__fabric_payload; "
            f"Write-Output '{marker}END';"
        )

        async with self.lock:
            self.last_used_at = _utcnow_iso()
            await _write_script(self.process, script)
            result = await _read_result(self.process, marker, timeout_seconds)
            if operation == "set_location" and "path" in result:
                self.working_directory = str(result["path"])
            return result

    async def run_raw_command(
        self,
        command: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        marker = f"__FABRIC_PS__{uuid4()}__"
        encoded_command = base64.b64encode(command.encode("utf-8")).decode("ascii")
        result_script = (
            "$ErrorActionPreference = 'Continue'; "
            "$LASTEXITCODE = 0; "
            "$__fabric_command = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{encoded_command}')); "
            "$__fabric_script = [scriptblock]::Create($__fabric_command); "
            "$__fabric_output = . $__fabric_script 2>&1; "
            "$__fabric_stdout = @($__fabric_output | "
            "Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | "
            "ForEach-Object { $_.ToString() }); "
            "$__fabric_stderr = @($__fabric_output | "
            "Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | "
            "ForEach-Object { $_.ToString() }); "
            "$__fabric_result = [pscustomobject]@{ "
            "stdout=($__fabric_stdout -join [Environment]::NewLine); "
            "stderr=($__fabric_stderr -join [Environment]::NewLine); "
            "exit_code=if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }; "
            "success=("
            "$__fabric_stderr.Count -eq 0 -and "
            "($null -eq $LASTEXITCODE -or $LASTEXITCODE -eq 0)"
            "); "
            "path=(Get-Location).Path "
            "}; "
            f"Write-Output '{marker}'; "
            "Write-Output ($__fabric_result | ConvertTo-Json -Compress -Depth 6); "
            f"Write-Output '{marker}END';"
        )

        async with self.lock:
            self.last_used_at = _utcnow_iso()
            await _write_script(self.process, result_script)
            result = await _read_raw_result(self.process, marker, timeout_seconds)
            if "path" in result:
                self.working_directory = str(result["path"])
            return result

    async def stream_raw_command(
        self,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        marker = f"__FABRIC_PS__{uuid4()}__"
        encoded_command = base64.b64encode(command.encode("utf-8")).decode("ascii")
        stdout_prefix = "__FABRIC_STDOUT__"
        stderr_prefix = "__FABRIC_STDERR__"
        result_script = (
            "$ErrorActionPreference = 'Continue'; "
            "$LASTEXITCODE = 0; "
            "$__fabric_command = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{encoded_command}')); "
            "$__fabric_script = [scriptblock]::Create($__fabric_command); "
            ". $__fabric_script 2>&1 | ForEach-Object { "
            "if ($_ -is [System.Management.Automation.ErrorRecord]) { "
            f"Write-Output ('{stderr_prefix}' + $_.ToString()) "
            "} else { "
            f"Write-Output ('{stdout_prefix}' + $_.ToString()) "
            "} "
            "}; "
            "$__fabric_result = [pscustomobject]@{ "
            "exit_code=if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }; "
            "path=(Get-Location).Path "
            "}; "
            f"Write-Output '{marker}'; "
            "Write-Output ($__fabric_result | ConvertTo-Json -Compress -Depth 6); "
            f"Write-Output '{marker}END';"
        )

        async with self.lock:
            self.last_used_at = _utcnow_iso()
            self._cancel_requested = False
            process = self.process
            await _write_script(process, result_script)

            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            sequence = 1

            async for event in _stream_raw_result(
                process,
                marker,
                timeout_seconds,
                stdout_prefix=stdout_prefix,
                stderr_prefix=stderr_prefix,
            ):
                if event["kind"] == "stdout":
                    delta = str(event["value"])
                    stdout_chunks.append(delta)
                    yield {
                        "event": "terminal.stdout",
                        "stream": "stdout",
                        "sequence": sequence,
                        "delta": delta,
                    }
                    sequence += 1
                    continue

                if event["kind"] == "stderr":
                    delta = str(event["value"])
                    stderr_chunks.append(delta)
                    yield {
                        "event": "terminal.stderr",
                        "stream": "stderr",
                        "sequence": sequence,
                        "delta": delta,
                    }
                    sequence += 1
                    continue

                summary = event["value"]
                if not isinstance(summary, dict):
                    raise PowerShellExecutionError(
                        "PowerShell stream summary must be a JSON object"
                    )
                if self._cancel_requested:
                    raise PowerShellExecutionError("Command cancelled")

                path = str(summary.get("path", self.working_directory))
                self.working_directory = path
                result = {
                    "stdout": "".join(stdout_chunks),
                    "stderr": "".join(stderr_chunks),
                    "exit_code": int(summary.get("exit_code", 0)),
                    "success": (
                        len(stderr_chunks) == 0
                        and int(summary.get("exit_code", 0)) == 0
                    ),
                    "path": path,
                }
                yield {"_fabric_final_result": result}

    async def cancel_running_command(self) -> None:
        if not self.lock.locked():
            return

        self._cancel_requested = True
        old_process = self.process
        if old_process.returncode is None:
            old_process.kill()
            await old_process.wait()

        self.process = await _start_powershell_process(self.executable_path)
        await _set_process_location(self.process, self.working_directory)

    async def close(self) -> None:
        stdin = self.process.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.write(b"exit\n")
            await stdin.drain()
            stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=5)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()

    def state(self) -> PowerShellSessionState:
        return PowerShellSessionState(
            session_id=self.session_id,
            working_directory=self.working_directory,
            created_at=self.created_at,
            last_used_at=self.last_used_at,
            available=self.process.returncode is None,
            busy=self.lock.locked(),
        )


class PowerShellSessionManager(PowerShellSessionManagerLike):
    def __init__(self, probe: PowerShellProbeLike | None = None) -> None:
        self._probe = probe or PowerShellProbe()
        self._sessions: dict[str, PowerShellPersistentSession] = {}

    async def create_session(
        self,
        *,
        working_directory: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        del timeout_seconds
        executable_path = self._resolve_executable()
        resolved_working_directory = _resolve_working_directory(working_directory)
        session = await PowerShellPersistentSession.create(
            executable_path=executable_path,
            working_directory=resolved_working_directory,
        )
        self._sessions[session.session_id] = session
        return {
            "provider": "windows_powershell",
            "session": session.state().to_dict(),
        }

    async def get_session_status(self, session_id: str) -> dict[str, Any]:
        session = self._get_session(session_id)
        return {
            "provider": "windows_powershell",
            "session": session.state().to_dict(),
        }

    async def close_session(self, session_id: str) -> dict[str, Any]:
        session = self._get_session(session_id)
        await session.close()
        self._sessions.pop(session_id, None)
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
        session = self._get_session(session_id)
        result = await session.run_operation(
            operation,
            payload,
            timeout_seconds=timeout_seconds,
        )
        return {
            "provider": "windows_powershell",
            "session_id": session_id,
            "operation": operation,
            "result": result,
        }

    async def run_raw_command(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        session = self._get_session(session_id)
        result = await session.run_raw_command(
            command,
            timeout_seconds=timeout_seconds,
        )
        return {
            "provider": "windows_powershell",
            "session_id": session_id,
            "command": command,
            "result": result,
        }

    async def cancel_running_command(self, session_id: str) -> None:
        session = self._get_session(session_id)
        await session.cancel_running_command()

    async def _stream_raw_command_impl(
        self,
        session_id: str,
        command: str,
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[dict[str, Any]]:
        session = self._get_session(session_id)
        async for event in session.stream_raw_command(
            command,
            timeout_seconds=timeout_seconds,
        ):
            yield event

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

    def _get_session(self, session_id: str) -> PowerShellPersistentSession:
        try:
            session = self._sessions[session_id]
        except KeyError as exc:
            raise PowerShellExecutionError(
                f"Unknown PowerShell session: {session_id}"
            ) from exc
        if session.process.returncode is not None:
            self._sessions.pop(session_id, None)
            raise PowerShellExecutionError(
                f"PowerShell session is no longer available: {session_id}"
            )
        return session

    def _resolve_executable(self) -> Path:
        probe_result = self._probe.probe()
        if probe_result.executable_path is None:
            raise PowerShellExecutionError("No PowerShell executable is available")
        return probe_result.executable_path


def _resolve_working_directory(working_directory: str | None) -> str:
    if working_directory is None:
        return str(Path.cwd())
    resolved = Path(working_directory).expanduser()
    if not resolved.exists() or not resolved.is_dir():
        raise PowerShellExecutionError(
            "working_directory must point to an existing directory"
        )
    return str(resolved)


def _build_session_operation_script(
    operation: str,
    payload: dict[str, Any],
) -> str:
    if operation == "get_location":
        return (
            "$__fabric_result = [pscustomobject]@{ "
            "path=(Get-Location).Path "
            "};"
        )

    if operation == "set_location":
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise PowerShellExecutionError("set_location requires a non-empty path")
        quoted_path = _ps_quote(path)
        return (
            f"Set-Location -LiteralPath {quoted_path}; "
            "$__fabric_result = [pscustomobject]@{ "
            "path=(Get-Location).Path "
            "};"
        )

    if operation == "test_path":
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise PowerShellExecutionError("test_path requires a non-empty path")
        quoted_path = _ps_quote(path)
        return (
            "$__fabric_result = [pscustomobject]@{ "
            f"path={quoted_path}; "
            f"exists=[bool](Test-Path -LiteralPath {quoted_path}) "
            "};"
        )

    if operation == "get_child_items":
        path = payload.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            raise PowerShellExecutionError("path must be a non-empty string")
        max_items = payload.get("max_items", 25)
        if not isinstance(max_items, int) or max_items <= 0 or max_items > 100:
            raise PowerShellExecutionError(
                "max_items must be an integer between 1 and 100"
            )
        if path is None:
            source = "Get-ChildItem"
        else:
            source = f"Get-ChildItem -LiteralPath {_ps_quote(path)}"
        return (
            f"$items = {source} | "
            f"Select-Object -First {max_items} Name, FullName, Mode, Length; "
            "$__fabric_result = [pscustomobject]@{ "
            "items=@($items | ForEach-Object { [pscustomobject]@{ "
            "name=$_.Name; "
            "full_name=$_.FullName; "
            "mode=$_.Mode; "
            "length=$_.Length "
            "} }) "
            "};"
        )

    if operation == "claude_version":
        return (
            "$claude = Get-Command claude -ErrorAction SilentlyContinue; "
            "if ($null -eq $claude) { "
            "  $__fabric_result = [pscustomobject]@{ "
            "available=$false; "
            "version=$null "
            "}; "
            "} else { "
            "  $version = (& claude --version 2>$null); "
            "  $__fabric_result = [pscustomobject]@{ "
            "available=$true; "
            "version=$version "
            "}; "
            "}"
        )

    if operation == "get_service_status":
        service_name = payload.get("service_name")
        if not isinstance(service_name, str) or not service_name.strip():
            raise PowerShellExecutionError(
                "get_service_status requires a non-empty service_name"
            )
        quoted_name = _ps_quote(service_name)
        return (
            f"$service = Get-Service -Name {quoted_name} -ErrorAction Stop; "
            "$__fabric_result = [pscustomobject]@{ "
            "name=$service.Name; "
            "display_name=$service.DisplayName; "
            "status=$service.Status.ToString(); "
            "service_type=$service.ServiceType.ToString() "
            "};"
        )

    if operation == "get_runtime_status":
        return (
            "$python = Get-Command python -ErrorAction SilentlyContinue; "
            "$docker = Get-Command docker -ErrorAction SilentlyContinue; "
            "$redis = Get-Process redis-server -ErrorAction SilentlyContinue | "
            "Select-Object -First 1; "
            "$__fabric_result = [pscustomobject]@{ "
            "python_available=($null -ne $python); "
            "python_version=if ($null -ne $python) { "
            "(& python --version 2>&1) "
            "} else { $null }; "
            "docker_available=($null -ne $docker); "
            "docker_version=if ($null -ne $docker) { "
            "(& docker --version 2>$null) "
            "} else { $null }; "
            "redis_running=($null -ne $redis); "
            "redis_pid=if ($null -ne $redis) { $redis.Id } else { $null } "
            "};"
        )

    if operation == "get_network_status":
        return (
            "$profiles = Get-NetIPConfiguration | "
            "Where-Object { $null -ne $_.IPv4Address } | "
            "Select-Object -First 10 InterfaceAlias, InterfaceDescription, "
            "IPv4Address, IPv4DefaultGateway, DNSServer; "
            "$__fabric_result = [pscustomobject]@{ "
            "computer_name=$env:COMPUTERNAME; "
            "adapters=@($profiles | ForEach-Object { [pscustomobject]@{ "
            "interface_alias=$_.InterfaceAlias; "
            "interface_description=$_.InterfaceDescription; "
            "ipv4=@($_.IPv4Address | ForEach-Object { $_.IPAddress }); "
            "gateway=if ($null -ne $_.IPv4DefaultGateway) { "
            "$_.IPv4DefaultGateway.NextHop "
            "} else { $null }; "
            "dns=@($_.DNSServer.ServerAddresses) "
            "} }) "
            "};"
        )

    if operation == "get_python_process_status":
        return (
            "$items = Get-Process | Where-Object { "
            "$_.ProcessName -like 'python*' "
            "} | Select-Object -First 25 ProcessName, Id, CPU, WS; "
            "$__fabric_result = [pscustomobject]@{ "
            "processes=@($items | ForEach-Object { [pscustomobject]@{ "
            "name=$_.ProcessName; "
            "pid=$_.Id; "
            "cpu=$_.CPU; "
            "working_set=$_.WS "
            "} }) "
            "};"
        )

    if operation == "get_claude_process_status":
        return (
            "$items = Get-Process | Where-Object { "
            "$_.ProcessName -like 'claude*' -or $_.ProcessName -like 'node*' "
            "} | Select-Object -First 25 ProcessName, Id, CPU, WS; "
            "$__fabric_result = [pscustomobject]@{ "
            "processes=@($items | ForEach-Object { [pscustomobject]@{ "
            "name=$_.ProcessName; "
            "pid=$_.Id; "
            "cpu=$_.CPU; "
            "working_set=$_.WS "
            "} }) "
            "};"
        )

    if operation == "get_event_log":
        log_name = payload.get("log_name", "Application")
        if not isinstance(log_name, str) or log_name not in {"Application", "System"}:
            raise PowerShellExecutionError(
                "log_name must be either Application or System"
            )
        max_items = payload.get("max_items", 20)
        if not isinstance(max_items, int) or max_items <= 0 or max_items > 100:
            raise PowerShellExecutionError(
                "max_items must be an integer between 1 and 100"
            )
        provider_name = payload.get("provider_name")
        provider_filter = ""
        if provider_name is not None:
            if not isinstance(provider_name, str) or not provider_name.strip():
                raise PowerShellExecutionError(
                    "provider_name must be a non-empty string"
                )
            provider_filter = (
                f" | Where-Object {{ $_.ProviderName -eq {_ps_quote(provider_name)} }}"
            )
        return (
            f"$items = Get-WinEvent -LogName {_ps_quote(log_name)} "
            f"-MaxEvents {max_items}{provider_filter} | "
            "Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message; "
            "$__fabric_result = [pscustomobject]@{ "
            "entries=@($items | ForEach-Object { [pscustomobject]@{ "
            "time_created=$_.TimeCreated; "
            "id=$_.Id; "
            "level=$_.LevelDisplayName; "
            "provider_name=$_.ProviderName; "
            "message=$_.Message "
            "} }) "
            "};"
        )

    if operation == "get_file_summary":
        path = payload.get("path")
        if not isinstance(path, str) or not path.strip():
            raise PowerShellExecutionError(
                "get_file_summary requires a non-empty path"
            )
        quoted_path = _ps_quote(path)
        return (
            f"$item = Get-Item -LiteralPath {quoted_path} -ErrorAction Stop; "
            "$children = @(); "
            "if ($item.PSIsContainer) { "
            "  $children = Get-ChildItem -LiteralPath $item.FullName | "
            "  Select-Object -First 20 Name, FullName, Mode, Length, LastWriteTime; "
            "} "
            "$__fabric_result = [pscustomobject]@{ "
            "path=$item.FullName; "
            "name=$item.Name; "
            "exists=$true; "
            "is_directory=[bool]$item.PSIsContainer; "
            "length=if ($item.PSIsContainer) { $null } else { $item.Length }; "
            "last_write_time=$item.LastWriteTime; "
            "children=@($children | ForEach-Object { [pscustomobject]@{ "
            "name=$_.Name; "
            "full_name=$_.FullName; "
            "mode=$_.Mode; "
            "length=$_.Length; "
            "last_write_time=$_.LastWriteTime "
            "} }) "
            "};"
        )

    raise PowerShellExecutionError(
        f"Unsupported PowerShell session operation: {operation}"
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _write_script(
    process: asyncio.subprocess.Process,
    script: str,
) -> None:
    stdin = process.stdin
    if stdin is None or stdin.is_closing():
        raise PowerShellExecutionError("PowerShell session stdin is not available")
    stdin.write((script + "\n").encode("utf-8"))
    await stdin.drain()


async def _start_powershell_process(
    executable_path: Path,
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        str(executable_path),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-NoExit",
        "-Command",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _set_process_location(
    process: asyncio.subprocess.Process,
    working_directory: str,
) -> None:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"Set-Location -LiteralPath {_ps_quote(working_directory)}; "
        "Write-Output '__FABRIC_INIT_OK__';"
    )
    await _write_script(process, script)
    stdout = process.stdout
    if stdout is None:
        raise PowerShellExecutionError("PowerShell session stdout is not available")
    while True:
        raw_line = await stdout.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "__FABRIC_INIT_OK__":
            return


async def _read_result(
    process: asyncio.subprocess.Process,
    marker: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    stdout = process.stdout
    stderr = process.stderr
    if stdout is None or stderr is None:
        raise PowerShellExecutionError("PowerShell session pipes are not available")

    async def _collect() -> dict[str, Any]:
        seen_marker = False
        payload_line: str | None = None
        while True:
            raw_line = await stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == marker:
                seen_marker = True
                continue
            if line == f"{marker}END":
                break
            if seen_marker and payload_line is None:
                payload_line = line

        if payload_line is None:
            stderr_output = (await stderr.read()).decode("utf-8", errors="replace")
            raise PowerShellExecutionError(
                "PowerShell session returned no structured payload. "
                f"stderr={stderr_output.strip()}"
            )
        try:
            payload = json.loads(payload_line)
        except json.JSONDecodeError as exc:
            raise PowerShellExecutionError(
                "PowerShell session returned invalid JSON payload"
            ) from exc
        if not isinstance(payload, dict):
            raise PowerShellExecutionError(
                "PowerShell session structured payload must be a JSON object"
            )
        if payload.get("ok") is not True:
            raise PowerShellExecutionError(str(payload.get("error", "Unknown error")))
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise PowerShellExecutionError(
                "PowerShell session result must be a JSON object"
            )
        return result

    return await asyncio.wait_for(_collect(), timeout=timeout_seconds)


async def _read_raw_result(
    process: asyncio.subprocess.Process,
    marker: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    stdout = process.stdout
    if stdout is None:
        raise PowerShellExecutionError("PowerShell session stdout is not available")

    async def _collect() -> dict[str, Any]:
        seen_marker = False
        payload_line: str | None = None
        while True:
            raw_line = await stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == marker:
                seen_marker = True
                continue
            if line == f"{marker}END":
                break
            if seen_marker and payload_line is None:
                payload_line = line

        if payload_line is None:
            raise PowerShellExecutionError(
                "PowerShell raw command returned no structured payload"
            )
        try:
            payload = json.loads(payload_line)
        except json.JSONDecodeError as exc:
            raise PowerShellExecutionError(
                "PowerShell raw command returned invalid JSON payload"
            ) from exc
        if not isinstance(payload, dict):
            raise PowerShellExecutionError(
                "PowerShell raw command payload must be a JSON object"
            )
        return payload

    return await asyncio.wait_for(_collect(), timeout=timeout_seconds)


async def _stream_raw_result(
    process: asyncio.subprocess.Process,
    marker: str,
    timeout_seconds: int,
    *,
    stdout_prefix: str,
    stderr_prefix: str,
) -> AsyncIterator[dict[str, Any]]:
    stdout = process.stdout
    if stdout is None:
        raise PowerShellExecutionError("PowerShell session stdout is not available")

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    seen_marker = False

    while True:
        remaining = max(0.1, deadline - asyncio.get_running_loop().time())
        try:
            raw_line = await asyncio.wait_for(stdout.readline(), timeout=remaining)
        except TimeoutError as exc:
            raise PowerShellExecutionError("PowerShell command timed out") from exc

        if not raw_line:
            break

        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == marker:
            seen_marker = True
            continue
        if line == f"{marker}END":
            break
        if seen_marker:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PowerShellExecutionError(
                    "PowerShell stream returned invalid JSON payload"
                ) from exc
            if not isinstance(payload, dict):
                raise PowerShellExecutionError(
                    "PowerShell stream summary must be a JSON object"
                )
            yield {"kind": "result", "value": payload}
            continue

        if line.startswith(stdout_prefix):
            yield {
                "kind": "stdout",
                "value": line.removeprefix(stdout_prefix) + "\n",
            }
            continue

        if line.startswith(stderr_prefix):
            yield {
                "kind": "stderr",
                "value": line.removeprefix(stderr_prefix) + "\n",
            }
            continue

    if not seen_marker and process.returncode is not None:
        raise PowerShellExecutionError("PowerShell command terminated unexpectedly")


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()
