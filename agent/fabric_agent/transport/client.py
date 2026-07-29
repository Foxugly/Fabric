from __future__ import annotations

import asyncio
import contextlib
import json
import platform
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from shared.protocol import build_message, parse_message
from websockets.asyncio.client import ClientConnection, connect

from fabric_agent.application.config import AgentConfig
from fabric_agent.application.dispatcher import CommandDispatcher
from fabric_agent.application.registry import ProviderRegistry
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


class AgentWebSocketClient:
    def __init__(
        self,
        config: AgentConfig,
        registry: ProviderRegistry,
        dispatcher: CommandDispatcher,
    ) -> None:
        self._config = config
        self._registry = registry
        self._dispatcher = dispatcher
        self._active_commands: dict[str, ActiveCommand] = {}

    async def run_forever(self) -> None:
        backoff = 1
        while True:
            try:
                await self._run_once()
                backoff = 1
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)

    async def _run_once(self) -> None:
        query = urlencode(
            {"agent_id": self._config.agent_id, "token": self._config.agent_token}
        )
        async with connect(f"{self._config.server_ws_url}?{query}") as websocket:
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
            async for progress in self._dispatcher.stream(
                provider_name, action, parameters
            ):
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
