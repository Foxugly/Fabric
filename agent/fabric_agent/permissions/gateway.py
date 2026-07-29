"""Local broker between Claude Code's permission prompts and Fabric.

In `--print` mode Claude Code cannot draw a permission dialog, so it delegates
to an MCP tool named by `--permission-prompt-tool`. That tool runs in a separate
process spawned by the CLI, so it needs a way back into the agent: this gateway
is a loopback TCP server speaking one JSON object per line.

    claude  ->  fabric_agent.permission_broker (MCP stdio)  ->  gateway  ->  Fabric

Nothing here is reachable off-machine: the socket binds 127.0.0.1 on an
ephemeral port and every connection must present a per-run token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger(__name__)

#: Claude Code addresses MCP tools as `mcp__<server>__<tool>`.
MCP_SERVER_NAME = "fabric"
MCP_TOOL_NAME = "approval_prompt"
PERMISSION_PROMPT_TOOL = f"mcp__{MCP_SERVER_NAME}__{MCP_TOOL_NAME}"


@dataclass(slots=True, frozen=True)
class PermissionRequest:
    request_id: str
    command_id: str
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "command_id": self.command_id,
            "tool_name": self.tool_name,
            "input": self.tool_input,
            "tool_use_id": self.tool_use_id,
        }


@dataclass(slots=True, frozen=True)
class PermissionDecision:
    allowed: bool
    message: str = ""
    updated_input: dict[str, Any] | None = None

    def to_mcp_response(self, original_input: dict[str, Any]) -> dict[str, Any]:
        """The exact shape Claude Code expects back from the prompt tool."""
        if self.allowed:
            return {
                "behavior": "allow",
                "updatedInput": (
                    self.updated_input
                    if self.updated_input is not None
                    else original_input
                ),
            }
        return {
            "behavior": "deny",
            "message": self.message or "Denied from Fabric",
        }


#: Publishes a request to Fabric. The decision arrives later via `resolve()`.
RequestHandler = Callable[[PermissionRequest], Awaitable[None]]


class PermissionGateway:
    def __init__(self, handler: RequestHandler | None = None) -> None:
        self._handler = handler
        self._token = secrets.token_urlsafe(32)
        self._server: asyncio.Server | None = None
        self._port: int | None = None
        self._pending: dict[str, asyncio.Future[PermissionDecision]] = {}

    @property
    def token(self) -> str:
        return self._token

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("Permission gateway is not started")
        return self._port

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def set_handler(self, handler: RequestHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client, host="127.0.0.1", port=0
        )
        self._port = self._server.sockets[0].getsockname()[1]
        LOGGER.info("Permission gateway listening on 127.0.0.1:%s", self._port)

    async def stop(self) -> None:
        self.fail_pending("Fabric agent is shutting down")
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None
        self._port = None

    def resolve(self, request_id: str, decision: PermissionDecision) -> bool:
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(decision)
        return True

    def fail_pending(self, reason: str) -> None:
        """Deny everything still waiting, e.g. when the socket to Fabric drops."""
        for request_id, future in list(self._pending.items()):
            self._pending.pop(request_id, None)
            if not future.done():
                future.set_result(PermissionDecision(allowed=False, message=reason))

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not raw_line:
                return
            message = json.loads(raw_line.decode("utf-8"))
            if not isinstance(message, dict):
                raise ValueError("Permission request must be a JSON object")
            if not secrets.compare_digest(str(message.get("token", "")), self._token):
                LOGGER.warning("Rejected permission request with a bad token")
                return

            decision = await self._decide(_parse_request(message))
            response = decision.to_mcp_response(
                _coerce_input(message.get("input")),
            )
        except Exception as exc:
            LOGGER.warning("Permission request failed: %s", exc)
            response = {
                "behavior": "deny",
                "message": f"Fabric could not obtain a decision: {exc}",
            }

        with contextlib.suppress(Exception):
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    async def _decide(self, request: PermissionRequest) -> PermissionDecision:
        if self._handler is None:
            return PermissionDecision(
                allowed=False,
                message="Fabric is not connected, so nobody can approve this tool.",
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionDecision] = loop.create_future()
        self._pending[request.request_id] = future
        try:
            await self._handler(request)
        except Exception as exc:
            self._pending.pop(request.request_id, None)
            return PermissionDecision(
                allowed=False,
                message=f"Fabric could not ask for approval: {exc}",
            )

        # No timeout here on purpose: the turn's own `timeout_seconds` bounds the
        # wait, and a dropped connection resolves everything via `fail_pending`.
        try:
            return await future
        finally:
            self._pending.pop(request.request_id, None)


def _parse_request(message: dict[str, Any]) -> PermissionRequest:
    request_id = str(message.get("request_id", "")).strip()
    command_id = str(message.get("command_id", "")).strip()
    tool_name = str(message.get("tool_name", "")).strip()
    if not request_id or not tool_name:
        raise ValueError("Permission request needs a request_id and a tool_name")
    tool_use_id = message.get("tool_use_id")
    return PermissionRequest(
        request_id=request_id,
        command_id=command_id,
        tool_name=tool_name,
        tool_input=_coerce_input(message.get("input")),
        tool_use_id=tool_use_id if isinstance(tool_use_id, str) else None,
    )


def _coerce_input(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
