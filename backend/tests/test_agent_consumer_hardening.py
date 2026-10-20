"""M1: the agent socket survives malformed input and bounds what it accepts.

A crash in the consumer tears the socket down, and `_mark_offline` then fails
every command in flight: one malformed frame must never cost that.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from shared.protocol import build_message
from ws_helpers import agent_headers

from apps.agents.models import Agent
from apps.commands.models import (
    Command,
    CommandEvent,
    CommandStatus,
    PermissionRequest,
)
from apps.commands.output_cap import TRUNCATION_MARKER, cap_strings
from config.asgi import application


async def _setup(username: str) -> tuple[Agent, Command, WebsocketCommunicator]:
    owner = await get_user_model().objects.acreate_user(
        username=username, password="pw"
    )
    agent = await Agent.objects.acreate(name=f"{username}-pc", owner=owner)
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(token)
    )
    connected, _ = await communicator.connect()
    assert connected is True
    authenticated = await communicator.receive_json_from()
    assert authenticated["type"] == "agent.authenticated"
    command = await Command.objects.acreate(
        requested_by=owner,
        agent=agent,
        provider="windows_powershell",
        action="windows_powershell.command.execute",
        payload={"command": "Get-Date"},
        status=CommandStatus.RUNNING,
    )
    return agent, command, communicator


async def _expect_protocol_error(communicator: WebsocketCommunicator) -> str:
    message = await communicator.receive_json_from(timeout=2)
    assert message["type"] == "error", message
    assert message["payload"]["code"] == "protocol_error"
    return str(message["payload"]["message"])


async def _expect_close(communicator: WebsocketCommunicator, code: int) -> None:
    output = await communicator.receive_output(timeout=2)
    assert output["type"] == "websocket.close", output
    assert output.get("code") == code, output


async def _still_alive(communicator: WebsocketCommunicator) -> None:
    """A heartbeat is processed silently: nothing comes back, no close."""
    await communicator.send_json_to(
        build_message(message_type="agent.heartbeat", payload={})
    )
    assert await communicator.receive_nothing(timeout=0.2) is True


def _progress(command: Command, **payload: Any) -> dict[str, Any]:
    return build_message(
        message_type="command.progress",
        payload={"command_id": str(command.id), **payload},
    )


async def _events(command: Command) -> list[CommandEvent]:
    return [event async for event in CommandEvent.objects.filter(command=command)]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_malformed_json_is_answered_not_fatal() -> None:
    _agent, command, communicator = await _setup("bad-json")

    await communicator.send_to(text_data="{not json")
    assert "JSON" in await _expect_protocol_error(communicator)
    await communicator.send_to(text_data="[1, 2, 3]")
    assert "object" in await _expect_protocol_error(communicator)
    await communicator.send_to(bytes_data=b"\xff\xfe")
    await _expect_protocol_error(communicator)

    await _still_alive(communicator)
    command = await Command.objects.aget(id=command.id)
    assert command.status == CommandStatus.RUNNING
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("message_type", "payload"),
    [
        ("command.started", {}),
        ("command.started", {"command_id": "not-a-uuid"}),
        ("command.progress", {"sequence": "abc"}),
        ("command.progress", {"sequence": -1}),
        ("command.progress", {"sequence": True}),
        ("command.progress", {"delta": {"nested": 1}}),
        ("session.action_required", {"request_id": "nope", "tool_name": "Bash"}),
        ("session.action_required", {"request_id": str(uuid4())}),
        (
            "session.action_required",
            {"request_id": str(uuid4()), "tool_name": "Bash", "input": "rm"},
        ),
        ("command.completed", {"result": "text"}),
        ("command.failed", {"error": 42}),
        ("provider.capabilities", {"capabilities": "all"}),
        ("agent.hello", {"version": 1}),
    ],
)
async def test_malformed_payloads_get_a_protocol_error(
    message_type: str, payload: dict[str, Any]
) -> None:
    _agent, command, communicator = await _setup(f"bad-{uuid4().hex[:8]}")
    # Every case but the two command_id ones targets a real command, so the
    # error can only come from the field under test.
    if message_type != "command.started":
        payload = {"command_id": str(command.id), **payload}

    await communicator.send_json_to(
        build_message(message_type=message_type, payload=payload)
    )
    await _expect_protocol_error(communicator)
    await _still_alive(communicator)

    command = await Command.objects.aget(id=command.id)
    assert command.status == CommandStatus.RUNNING
    assert not await PermissionRequest.objects.filter(command=command).aexists()
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_oversized_frame_closes_the_socket(settings: Any) -> None:
    settings.FABRIC_AGENT_MAX_MESSAGE_BYTES = 2_000
    _agent, command, communicator = await _setup("too-big")

    await communicator.send_json_to(_progress(command, sequence=1, delta="x" * 5_000))

    await _expect_close(communicator, 1009)
    assert await _events(command) == []


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_message_flood_closes_the_socket(settings: Any) -> None:
    settings.FABRIC_AGENT_MAX_MESSAGES_PER_MINUTE = 5
    _agent, _command, communicator = await _setup("flood")

    for _ in range(5):
        await communicator.send_json_to(
            build_message(message_type="agent.heartbeat", payload={})
        )
    assert await communicator.receive_nothing(timeout=0.2) is True
    await communicator.send_json_to(
        build_message(message_type="agent.heartbeat", payload={})
    )

    await _expect_close(communicator, 1008)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_progress_on_a_finished_command_is_ignored() -> None:
    _agent, command, communicator = await _setup("late-progress")
    await Command.objects.filter(id=command.id).aupdate(
        status=CommandStatus.SUCCEEDED
    )

    await communicator.send_json_to(_progress(command, sequence=1, delta="late"))
    await _still_alive(communicator)

    assert await _events(command) == []
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_replayed_sequence_does_not_crash_the_socket() -> None:
    _agent, command, communicator = await _setup("replay")

    await communicator.send_json_to(_progress(command, sequence=1, delta="a"))
    await communicator.send_json_to(_progress(command, sequence=1, delta="b"))
    await _still_alive(communicator)

    events = await _events(command)
    assert [event.payload["delta"] for event in events] == ["a"]
    command = await Command.objects.aget(id=command.id)
    assert command.status == CommandStatus.RUNNING
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_stored_output_is_capped_with_a_marker(settings: Any) -> None:
    """I4: a command stores at most FABRIC_COMMAND_OUTPUT_MAX_CHARS of output."""
    settings.FABRIC_COMMAND_OUTPUT_MAX_CHARS = 10
    _agent, command, communicator = await _setup("capped")

    await communicator.send_json_to(_progress(command, sequence=1, delta="123456"))
    await communicator.send_json_to(_progress(command, sequence=2, delta="7890abcd"))
    await communicator.send_json_to(_progress(command, sequence=3, delta="dropped"))
    await communicator.send_json_to(
        build_message(
            message_type="command.completed",
            payload={
                "command_id": str(command.id),
                "result": {"stdout": "y" * 50, "exit_code": 0},
            },
        )
    )
    for _ in range(40):
        command = await Command.objects.aget(id=command.id)
        if command.status == CommandStatus.SUCCEEDED:
            break
        await asyncio.sleep(0.05)

    deltas = [event.payload["delta"] for event in await _events(command)]
    assert deltas == ["123456", "7890" + TRUNCATION_MARKER]
    assert command.result == {"stdout": "y" * 10 + TRUNCATION_MARKER, "exit_code": 0}
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_failure_message_is_capped() -> None:
    _agent, command, communicator = await _setup("long-error")

    await communicator.send_json_to(
        build_message(
            message_type="command.failed",
            payload={"command_id": str(command.id), "error": "e" * 50_000},
        )
    )
    for _ in range(40):
        command = await Command.objects.aget(id=command.id)
        if command.status == CommandStatus.FAILED:
            break
        await asyncio.sleep(0.05)

    assert command.error.endswith(TRUNCATION_MARKER)
    assert len(command.error) < 11_000
    await communicator.disconnect()


def test_cap_strings_spends_one_budget_across_the_structure() -> None:
    value = {"a": "12345", "b": ["678", "90abc"], "n": 7}
    capped, used, truncated = cap_strings(value, 9)

    assert capped == {"a": "12345", "b": ["678", "9" + TRUNCATION_MARKER], "n": 7}
    assert used == 9
    assert truncated is True
    assert cap_strings(value, 100) == (value, 13, False)
    assert json.dumps(cap_strings("x", 0)[0]) == json.dumps(TRUNCATION_MARKER)
