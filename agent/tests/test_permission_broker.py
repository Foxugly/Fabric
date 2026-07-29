"""The broker is the MCP surface Claude Code talks to, so its shape matters."""

from __future__ import annotations

import asyncio
import io
import json
import threading
from typing import Any

import pytest

from fabric_agent import permission_broker
from fabric_agent.permissions import (
    PermissionDecision,
    PermissionGateway,
    PermissionRequest,
)
from fabric_agent.permissions.gateway import MCP_TOOL_NAME, PERMISSION_PROMPT_TOOL


def _serve(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stdin = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
    stdout = io.StringIO()
    permission_broker.serve(stdin, stdout)
    return [
        json.loads(raw) for raw in stdout.getvalue().splitlines() if raw.strip()
    ]


def test_initialize_echoes_the_requested_protocol_version() -> None:
    responses = _serve(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        ]
    )

    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert responses[0]["result"]["capabilities"] == {"tools": {}}


def test_notifications_get_no_response() -> None:
    assert _serve([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) == []


def test_tools_list_advertises_the_prompt_tool() -> None:
    responses = _serve([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])

    tools = responses[0]["result"]["tools"]
    assert [tool["name"] for tool in tools] == [MCP_TOOL_NAME]
    # Claude Code addresses it through the fully qualified MCP name.
    assert PERMISSION_PROMPT_TOOL == f"mcp__fabric__{MCP_TOOL_NAME}"


def test_a_tool_call_without_a_reachable_gateway_denies() -> None:
    responses = _serve(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": MCP_TOOL_NAME,
                    "arguments": {"tool_name": "Bash", "input": {}},
                },
            }
        ]
    )

    decision = json.loads(responses[0]["result"]["content"][0]["text"])
    assert decision["behavior"] == "deny"


def test_unknown_tool_is_an_error() -> None:
    responses = _serve(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "something_else", "arguments": {}},
            }
        ]
    )

    assert responses[0]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_broker_and_gateway_agree_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the real broker code against the real gateway."""
    published: list[PermissionRequest] = []

    async def handler(request: PermissionRequest) -> None:
        published.append(request)

    gateway = PermissionGateway(handler=handler)
    await gateway.start()
    monkeypatch.setenv("FABRIC_PERMISSION_PORT", str(gateway.port))
    monkeypatch.setenv("FABRIC_PERMISSION_TOKEN", gateway.token)
    monkeypatch.setenv("FABRIC_PERMISSION_COMMAND_ID", "cmd-42")

    result: dict[str, Any] = {}

    def run_broker() -> None:
        # The broker is synchronous by design, so it runs off the event loop.
        result.update(
            permission_broker.ask_fabric(
                {"tool_name": "Edit", "input": {"file_path": "main.py"}}
            )
        )

    thread = threading.Thread(target=run_broker, daemon=True)
    thread.start()

    try:
        for _ in range(100):
            if published:
                break
            await asyncio.sleep(0.02)
        assert len(published) == 1
        assert published[0].command_id == "cmd-42"
        assert published[0].tool_name == "Edit"

        gateway.resolve(published[0].request_id, PermissionDecision(allowed=True))
        await asyncio.get_running_loop().run_in_executor(None, thread.join, 5)
    finally:
        await gateway.stop()

    assert result == {"behavior": "allow", "updatedInput": {"file_path": "main.py"}}
