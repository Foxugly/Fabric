"""MCP stdio server exposing Fabric's approval prompt to Claude Code.

Claude Code spawns this module itself (through `--mcp-config`) and calls its
single tool whenever a turn needs permission, because `--print` mode has no
interactive dialog. The tool forwards the question to the running Fabric agent
over a loopback socket and blocks until a human answers in the web UI.

    claude  ->  this broker (MCP stdio)  ->  PermissionGateway  ->  Fabric  ->  browser

Deliberately synchronous and dependency-free: asyncio pipes on Windows stdio are
a well-known source of trouble, and this process only ever handles one request
at a time. Anything written to stdout that is not a JSON-RPC response corrupts
the protocol, so diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid
from typing import Any

from fabric_agent.permissions.gateway import MCP_TOOL_NAME

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
CONNECT_TIMEOUT_SECONDS = 10

TOOL_DEFINITION = {
    "name": MCP_TOOL_NAME,
    "description": (
        "Ask the Fabric operator to approve or deny a tool call. Returns a JSON "
        "string with a `behavior` of `allow` or `deny`."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "description": "The tool requesting permission.",
            },
            "input": {
                "type": "object",
                "description": "The input the tool would run with.",
            },
            "tool_use_id": {
                "type": "string",
                "description": "Identifier of the pending tool use.",
            },
        },
        "required": ["tool_name", "input"],
    },
}


def ask_fabric(arguments: dict[str, Any]) -> dict[str, Any]:
    """Forward one permission question to the agent and wait for the verdict."""
    port = int(os.environ["FABRIC_PERMISSION_PORT"])
    token = os.environ["FABRIC_PERMISSION_TOKEN"]
    command_id = os.environ.get("FABRIC_PERMISSION_COMMAND_ID", "")

    request = {
        "token": token,
        "request_id": str(uuid.uuid4()),
        "command_id": command_id,
        "tool_name": str(arguments.get("tool_name", "")),
        "input": arguments.get("input") or {},
        "tool_use_id": arguments.get("tool_use_id"),
    }

    with socket.create_connection(
        ("127.0.0.1", port), timeout=CONNECT_TIMEOUT_SECONDS
    ) as connection:
        connection.sendall((json.dumps(request) + "\n").encode("utf-8"))
        # A human is on the other end: block until they answer. The turn's own
        # `timeout_seconds` is what ultimately bounds this wait.
        connection.settimeout(None)
        raw_response = _read_line(connection)

    decision = json.loads(raw_response)
    if not isinstance(decision, dict) or "behavior" not in decision:
        raise ValueError("Fabric returned an unusable decision")
    return decision


def _read_line(connection: socket.socket) -> str:
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            if not chunks:
                raise ConnectionError("Fabric closed the connection without deciding")
            break
        chunks.append(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).decode("utf-8").strip()


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        params = message.get("params")
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return _result(
            message_id,
            {
                "protocolVersion": (
                    requested
                    if isinstance(requested, str)
                    else DEFAULT_PROTOCOL_VERSION
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fabric", "version": "0.1.0"},
            },
        )

    if method == "tools/list":
        return _result(message_id, {"tools": [TOOL_DEFINITION]})

    if method == "tools/call":
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if params.get("name") != MCP_TOOL_NAME:
            return _error(message_id, -32601, f"Unknown tool: {params.get('name')}")
        arguments = params.get("arguments")
        try:
            decision = ask_fabric(arguments if isinstance(arguments, dict) else {})
        except Exception as exc:  # noqa: BLE001 - surfaced back to the model
            print(f"fabric permission broker: {exc}", file=sys.stderr)
            decision = {
                "behavior": "deny",
                "message": f"Fabric could not reach the operator: {exc}",
            }
        # Claude Code parses the decision out of the tool's text content.
        return _result(
            message_id,
            {"content": [{"type": "text", "text": json.dumps(decision)}]},
        )

    if message_id is None:
        # A notification we do not answer (`notifications/initialized`).
        return None
    return _error(message_id, -32601, f"Unknown method: {method}")


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def serve(stdin: Any, stdout: Any) -> None:
    for line in stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue

        response = handle(message)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()


def main() -> None:
    try:
        serve(sys.stdin, sys.stdout)
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
