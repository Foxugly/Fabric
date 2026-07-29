from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from websockets.asyncio.client import ClientConnection

from fabric_agent.application.config import AgentConfig
from fabric_agent.providers.windows_powershell.runner import PowerShellExecutionError
from fabric_agent.providers.windows_powershell.session import _ps_quote


@dataclass(slots=True, frozen=True)
class ServerEndpoint:
    host: str
    port: int


class NetworkRecoveryPolicy:
    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._last_recovery_at: datetime | None = None

    async def try_recover(self, endpoint: ServerEndpoint) -> bool:
        if not self._config.network_recovery_enabled:
            return False
        if not self._can_recover_now():
            return False
        self._last_recovery_at = _utcnow()
        await _run_network_recovery(
            endpoint,
            adapter_name=self._config.network_recovery_adapter_name,
        )
        return True

    def _can_recover_now(self) -> bool:
        if self._last_recovery_at is None:
            return True
        cooldown = timedelta(seconds=self._config.network_recovery_cooldown_seconds)
        return _utcnow() >= self._last_recovery_at + cooldown


class ConnectivityWatchdog:
    def __init__(
        self,
        config: AgentConfig,
        recovery_policy: NetworkRecoveryPolicy | None = None,
    ) -> None:
        self._config = config
        self._recovery_policy = recovery_policy or NetworkRecoveryPolicy(config)
        self._failures = 0
        self._endpoint = _parse_endpoint(config.server_ws_url)

    async def monitor(self, websocket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._config.connectivity_check_seconds)
            healthy = await self._check(websocket)
            if healthy:
                self._failures = 0
                continue
            self._failures += 1
            if self._failures >= self._config.connectivity_failure_threshold:
                await self._recovery_policy.try_recover(self._endpoint)
                self._failures = 0
                await websocket.close(code=1011, reason="connectivity watchdog")
                return

    async def _check(self, websocket: ClientConnection) -> bool:
        if websocket.state.name != "OPEN":
            return False
        try:
            pong_waiter = await websocket.ping()
            await asyncio.wait_for(
                pong_waiter,
                timeout=max(1, self._config.connectivity_check_seconds - 1),
            )
            await asyncio.wait_for(
                asyncio.open_connection(self._endpoint.host, self._endpoint.port),
                timeout=3,
            )
            return True
        except Exception:
            return False


def _parse_endpoint(server_ws_url: str) -> ServerEndpoint:
    parsed = urlparse(server_ws_url)
    if not parsed.hostname:
        raise ValueError("FABRIC_SERVER_WS_URL must include a hostname")
    if parsed.port is not None:
        port = parsed.port
    else:
        port = 443 if parsed.scheme == "wss" else 80
    return ServerEndpoint(host=parsed.hostname, port=port)


async def _run_network_recovery(
    endpoint: ServerEndpoint,
    *,
    adapter_name: str | None,
) -> None:
    del endpoint
    commands = [
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "ipconfig /flushdns | Out-Null; ipconfig /renew | Out-Null",
        ]
    ]
    if adapter_name:
        safe_name = _ps_quote(adapter_name)
        commands.append(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    f"Disable-NetAdapter -Name {safe_name} -Confirm:$false; "
                    "Start-Sleep -Seconds 3; "
                    f"Enable-NetAdapter -Name {safe_name} -Confirm:$false"
                ),
            ]
        )

    for command in commands:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        if process.returncode != 0:
            raise PowerShellExecutionError(
                "Network recovery failed: "
                + stderr.decode("utf-8", errors="replace").strip()
            )


def _utcnow() -> datetime:
    return datetime.now(UTC)
