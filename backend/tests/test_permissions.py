"""The approval loop: agent asks, operator rules, agent is unblocked."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from shared.protocol import build_message

from apps.agents.models import Agent
from apps.commands.models import (
    Command,
    CommandStatus,
    PermissionDecision,
    PermissionRequest,
)
from config.asgi import application


async def _receive_message_type(
    communicator: WebsocketCommunicator,
    expected_type: str,
) -> dict[str, Any]:
    for _ in range(10):
        message = cast(dict[str, Any], await communicator.receive_json_from())
        if message["type"] == expected_type:
            return message
    raise AssertionError(f"Did not receive {expected_type}")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_permission_request_blocks_then_resumes_the_command() -> None:
    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="permission-user",
        password="permission-password",
    )
    agent = await Agent.objects.acreate(name="permission-agent", owner=user)
    token = await sync_to_async(agent.issue_development_token)()

    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()
    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "claude_code_local.message.send",
            "payload": {"text": "push the branch"},
        },
        format="json",
    )
    assert response.status_code == 201
    command_id = response.json()["id"]

    outbound = await _receive_message_type(communicator, "command.request")
    correlation_id = outbound["correlation_id"]
    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )

    # The agent is now blocked on a tool call.
    request_id = str(uuid4())
    await communicator.send_json_to(
        build_message(
            message_type="session.action_required",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "request_id": request_id,
                "tool_name": "Bash",
                "input": {"command": "git push"},
                "tool_use_id": "toolu_1",
            },
        )
    )

    permission: PermissionRequest | None = None
    for _ in range(40):
        permission = await PermissionRequest.objects.filter(
            request_id=request_id
        ).afirst()
        if permission is not None:
            break
        await asyncio.sleep(0.05)

    assert permission is not None
    assert permission.tool_name == "Bash"
    assert permission.tool_input == {"command": "git push"}
    command = await Command.objects.aget(id=command_id)
    assert command.status == CommandStatus.WAITING_USER_ACTION

    # The operator allows it from the web UI.
    decision_response = await sync_to_async(client.post)(
        f"/api/v1/commands/{command_id}/permissions/{request_id}/",
        {"behavior": "allow"},
        format="json",
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["decision"] == PermissionDecision.ALLOWED

    relayed = await _receive_message_type(communicator, "session.action_response")
    assert relayed["payload"]["request_id"] == request_id
    assert relayed["payload"]["behavior"] == "allow"

    command = await Command.objects.aget(id=command_id)
    assert command.status == CommandStatus.RUNNING

    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_denying_relays_the_reason_to_the_agent() -> None:
    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="deny-user",
        password="deny-password",
    )
    agent = await Agent.objects.acreate(name="deny-agent", owner=user)
    token = await sync_to_async(agent.issue_development_token)()

    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()
    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    client = APIClient()
    client.force_authenticate(user=user)
    response = await sync_to_async(client.post)(
        "/api/v1/commands/",
        {
            "agent_id": str(agent.id),
            "provider": "claude_code_local",
            "action": "claude_code_local.message.send",
            "payload": {"text": "delete everything"},
        },
        format="json",
    )
    command_id = response.json()["id"]
    outbound = await _receive_message_type(communicator, "command.request")
    correlation_id = outbound["correlation_id"]
    await communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )

    request_id = str(uuid4())
    await communicator.send_json_to(
        build_message(
            message_type="session.action_required",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "request_id": request_id,
                "tool_name": "Bash",
                "input": {"command": "rm -rf /"},
            },
        )
    )
    for _ in range(40):
        if await PermissionRequest.objects.filter(request_id=request_id).aexists():
            break
        await asyncio.sleep(0.05)

    decision_response = await sync_to_async(client.post)(
        f"/api/v1/commands/{command_id}/permissions/{request_id}/",
        {"behavior": "deny", "message": "not on this machine"},
        format="json",
    )
    assert decision_response.status_code == 200

    relayed = await _receive_message_type(communicator, "session.action_response")
    assert relayed["payload"]["behavior"] == "deny"
    assert relayed["payload"]["message"] == "not on this machine"

    # Answering twice must not unblock a second, unrelated tool call.
    replay = await sync_to_async(client.post)(
        f"/api/v1/commands/{command_id}/permissions/{request_id}/",
        {"behavior": "allow"},
        format="json",
    )
    assert replay.status_code == 409

    await communicator.disconnect()


@pytest.mark.django_db
def test_another_user_cannot_answer_your_permission_request() -> None:
    user_model = get_user_model()
    owner = user_model.objects.create_user(username="p-owner", password="pw")
    intruder = user_model.objects.create_user(username="p-intruder", password="pw")
    agent = Agent.objects.create(name="p-agent", owner=owner)
    command = Command.objects.create(
        requested_by=owner,
        agent=agent,
        provider="claude_code_local",
        action="claude_code_local.message.send",
        payload={"text": "hi"},
        status=CommandStatus.WAITING_USER_ACTION,
    )
    request = PermissionRequest.objects.create(
        command=command,
        request_id=uuid4(),
        tool_name="Bash",
        tool_input={"command": "whoami"},
    )

    client = APIClient()
    client.force_authenticate(user=intruder)
    response = client.post(
        f"/api/v1/commands/{command.id}/permissions/{request.request_id}/",
        {"behavior": "allow"},
        format="json",
    )

    assert response.status_code == 404
    request.refresh_from_db()
    assert request.decision == PermissionDecision.PENDING
