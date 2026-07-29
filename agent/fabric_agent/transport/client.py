from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import platform
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from shared.protocol import build_message, parse_message
from websockets.asyncio.client import ClientConnection, connect

from fabric_agent.application.config import AgentConfig
from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.application.registry import ProviderRegistry
from fabric_agent.permissions import (
    PermissionDecision,
    PermissionGateway,
    PermissionRequest,
)
from fabric_agent.transport.connectivity import ConnectivityWatchdog


@dataclass(slots=True)
class ActiveCommand:
    provider_name: str
    action: str
    payload: dict[str, Any]
    correlation_id: str
    task: asyncio.Task[None]


class CommandCancelledError(RuntimeError):
    pass


LOGGER = logging.getLogger(__name__)


class AgentWebSocketClient:
    def __init__(
        self,
        config: AgentConfig,
        registry: ProviderRegistry,
        dispatcher: CommandDispatcher,
        permission_gateway: PermissionGateway | None = None,
    ) -> None:
        self._config = config
        self._registry = registry
        self._dispatcher = dispatcher
        self._permission_gateway = permission_gateway
        self._active_commands: dict[str, ActiveCommand] = {}
        self._websocket: ClientConnection | None = None
        if permission_gateway is not None:
            permission_gateway.set_handler(self._publish_permission_request)

    async def run_forever(self) -> None:
        backoff = 1
        while True:
            try:
                LOGGER.info("Connecting to %s", self._config.server_ws_url)
                await self._run_once()
                LOGGER.info("Connection closed, reconnecting")
                backoff = 1
            except Exception as exc:
                LOGGER.warning(
                    "Agent connection failed (%s), retrying in %ss",
                    exc,
                    backoff,
                    exc_info=LOGGER.isEnabledFor(logging.DEBUG),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)

    async def _run_once(self) -> None:
        query = urlencode(
            {"agent_id": self._config.agent_id, "token": self._config.agent_token}
        )
        async with connect(
            f"{self._config.server_ws_url}?{query}",
            ssl=_ssl_context(self._config.server_ws_url),
        ) as websocket:
            self._websocket = websocket
            await self._send_hello(websocket)
            await self._send_capabilities(websocket)
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            watchdog_task = asyncio.create_task(
                ConnectivityWatchdog(self._config).monitor(websocket)
            )
            try:
                async for raw_message in websocket:
                    message = parse_message(_coerce_message(raw_message))
                    await self._handle_message(
                        websocket,
                        message.type,
                        message.payload,
                        message.correlation_id,
                    )
            finally:
                self._websocket = None
                if self._permission_gateway is not None:
                    # Nobody can answer a pending approval once Fabric is gone.
                    self._permission_gateway.fail_pending(
                        "Connection to Fabric was lost before you answered"
                    )
                heartbeat_task.cancel()
                watchdog_task.cancel()
                for active_command in self._active_commands.values():
                    active_command.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog_task

    async def _send_hello(self, websocket: ClientConnection) -> None:
        await websocket.send(
            _json_dumps(
                build_message(
                    message_type="agent.hello",
                    correlation_id=self._config.agent_id,
                    payload={
                        "version": "0.1.0",
                        "operating_system": platform.platform(),
                    },
                )
            )
        )

    async def _send_capabilities(self, websocket: ClientConnection) -> None:
        capabilities: list[str] = []
        for _, provider in self._registry.items():
            capabilities.extend(await provider.get_capabilities())
        await websocket.send(
            _json_dumps(
                build_message(
                    message_type="provider.capabilities",
                    correlation_id=self._config.agent_id,
                    payload={"capabilities": capabilities},
                )
            )
        )

    async def _heartbeat_loop(self, websocket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._config.heartbeat_seconds)
            await websocket.send(
                _json_dumps(
                    build_message(
                        message_type="agent.heartbeat",
                        correlation_id=self._config.agent_id,
                        payload={},
                    )
                )
            )

    async def _handle_message(
        self,
        websocket: ClientConnection,
        message_type: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> None:
        if message_type == "command.cancel":
            await self._cancel_active_command(payload)
            return

        if message_type == "session.action_response":
            self._resolve_permission_request(payload)
            return

        if message_type != "command.request":
            return

        command_id = str(payload["command_id"])
        provider_name = str(payload["provider"])
        action = str(payload["action"])
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("Command parameters must be a JSON object")

        execution_parameters = dict(parameters)
        execution_parameters["_fabric_command_id"] = command_id
        timeout_seconds = payload.get("timeout_seconds")
        if isinstance(timeout_seconds, int) and timeout_seconds > 0:
            execution_parameters["_fabric_timeout_seconds"] = timeout_seconds

        await self._send_message(
            websocket,
            "command.accepted",
            correlation_id,
            {"command_id": command_id},
        )

        task = asyncio.create_task(
            self._execute_command(
                websocket,
                command_id,
                provider_name,
                action,
                execution_parameters,
                correlation_id,
            )
        )
        self._active_commands[command_id] = ActiveCommand(
            provider_name=provider_name,
            action=action,
            payload=execution_parameters,
            correlation_id=correlation_id,
            task=task,
        )

    async def _execute_command(
        self,
        websocket: ClientConnection,
        command_id: str,
        provider_name: str,
        action: str,
        parameters: dict[str, Any],
        correlation_id: str,
    ) -> None:
        await self._send_message(
            websocket,
            "command.started",
            correlation_id,
            {"command_id": command_id},
        )

        try:
            last_snapshot = ""
            # `aclosing` finalises the provider generator if this task is
            # cancelled or the socket dies mid-stream, so session locks and
            # child processes are always released.
            async with contextlib.aclosing(
                self._dispatcher.stream(provider_name, action, parameters)
            ) as progress_events:
                async for progress in progress_events:
                    last_snapshot = str(progress.get("snapshot", last_snapshot))
                    await self._send_message(
                        websocket,
                        "command.progress",
                        correlation_id,
                        {"command_id": command_id, **progress},
                    )

            result = await self._dispatcher.execute(provider_name, action, parameters)
            if "text" not in result and last_snapshot:
                result["text"] = last_snapshot

            await self._send_message(
                websocket,
                "command.completed",
                correlation_id,
                {"command_id": command_id, "result": result},
            )
        except CommandCancelledError as exc:
            await self._send_message(
                websocket,
                "command.failed",
                correlation_id,
                {
                    "command_id": command_id,
                    "error": str(exc),
                    "cancelled": True,
                },
            )
        except asyncio.CancelledError:
            await self._send_message(
                websocket,
                "command.failed",
                correlation_id,
                {
                    "command_id": command_id,
                    "error": "Command cancelled",
                    "cancelled": True,
                },
            )
            raise
        except Exception as exc:
            LOGGER.warning(
                "Command %s (%s %s) failed: %s",
                command_id,
                provider_name,
                action,
                exc,
                exc_info=LOGGER.isEnabledFor(logging.DEBUG),
            )
            await self._send_message(
                websocket,
                "command.failed",
                correlation_id,
                {
                    "command_id": command_id,
                    "error": _format_command_error(exc),
                },
            )
        finally:
            self._active_commands.pop(command_id, None)

    async def _publish_permission_request(self, request: PermissionRequest) -> None:
        """Ask Fabric — and therefore the operator — to rule on a tool call."""
        websocket = self._websocket
        if websocket is None:
            raise ConnectionError("Not connected to Fabric")
        LOGGER.info(
            "Permission requested for %s (command %s)",
            request.tool_name,
            request.command_id,
        )
        await self._send_message(
            websocket,
            "session.action_required",
            request.command_id or str(uuid4()),
            request.to_payload(),
        )

    def _resolve_permission_request(self, payload: dict[str, Any]) -> None:
        if self._permission_gateway is None:
            return
        request_id = str(payload.get("request_id", ""))
        decision = PermissionDecision(
            allowed=payload.get("behavior") == "allow",
            message=str(payload.get("message", "")),
            updated_input=(
                payload["updated_input"]
                if isinstance(payload.get("updated_input"), dict)
                else None
            ),
        )
        if not self._permission_gateway.resolve(request_id, decision):
            LOGGER.warning("No permission request is waiting for %s", request_id)

    async def _cancel_active_command(self, payload: dict[str, Any]) -> None:
        command_id = str(payload["command_id"])
        active = self._active_commands.get(command_id)
        if active is None:
            return

        try:
            await self._dispatcher.cancel(
                active.provider_name,
                active.action,
                active.payload,
            )
        except NotImplementedError:
            # A provider without cooperative cancellation still gets its task
            # cancelled below; it must never take the whole socket down.
            pass
        finally:
            active.task.cancel()

    async def _send_message(
        self,
        websocket: ClientConnection,
        message_type: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        await websocket.send(
            _json_dumps(
                build_message(
                    message_type=message_type,
                    correlation_id=correlation_id,
                    payload=payload,
                )
            )
        )


def _ssl_context(server_ws_url: str) -> ssl.SSLContext | None:
    """TLS trust for the outbound connection, independent of the OS store.

    Windows still ships `DST Root CA X3`, expired since 2021-09-30. OpenSSL
    happily builds a path through that cross-sign instead of stopping at the
    valid `ISRG Root X1`, so every Let's Encrypt host is rejected with
    "certificate has expired" — while browsers and curl succeed, which makes it
    look like a server problem. The agent runs on machines whose root store we
    do not control, so it carries a curated bundle instead.

    Returns None for plain `ws://`, where TLS does not apply.
    """
    if urlparse(server_ws_url).scheme != "wss":
        return None
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi is a declared dependency
        LOGGER.warning("certifi is missing, falling back to the OS trust store")
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _coerce_message(raw_message: Any) -> dict[str, Any]:
    if not isinstance(raw_message, str):
        raise ValueError("Expected text frame")
    data = json.loads(raw_message)
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload)


def _format_command_error(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__
