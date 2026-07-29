from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from shared.protocol import build_message

from apps.agents.models import Agent
from apps.commands.models import CommandStatus
from apps.conversations.models import MessageRole, MessageStatus
from config.asgi import application


@pytest.mark.django_db
def test_conversation_create_and_list(authenticated_api_client: APIClient) -> None:
    agent = Agent.objects.create(name="agent")

    response = authenticated_api_client.post(
        "/api/v1/conversations/",
        {
            "agent_id": str(agent.id),
            "provider": "echo",
            "title": "Echo chat",
        },
        format="json",
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["provider"] == "echo"
    assert payload["title"] == "Echo chat"

    listing = authenticated_api_client.get("/api/v1/conversations/")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_message_create_dispatches_command_and_updates_assistant_message(
) -> None:
    agent = await Agent.objects.acreate(name="agent")
    agent_token = await sync_to_async(agent.issue_development_token)()
    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="conversation-user",
        password="conversation-password",
    )
    user_token = await Token.objects.acreate(user=user)

    agent_communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={agent_token}",
    )
    connected, _ = await agent_communicator.connect()
    assert connected is True
    await agent_communicator.receive_json_from()
    await agent_communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    event_communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/events/?token={user_token.key}",
    )
    event_connected, _ = await event_communicator.connect()
    assert event_connected is True

    client = APIClient()
    client.force_authenticate(user=user)
    conversation_response = await sync_to_async(client.post)(
        "/api/v1/conversations/",
        {
            "agent_id": str(agent.id),
            "provider": "echo",
            "title": "Echo flow",
        },
        format="json",
    )
    assert conversation_response.status_code == 201
    conversation_id = conversation_response.json()["id"]

    message_response = await sync_to_async(client.post)(
        f"/api/v1/conversations/{conversation_id}/messages/",
        {"content": "Bonjour Fabric"},
        format="json",
    )
    assert message_response.status_code == 201
    assistant_message_id = message_response.json()["message"]["id"]
    command_id = message_response.json()["command_id"]

    outbound = await agent_communicator.receive_json_from()
    assert outbound["type"] == "command.request"
    assert outbound["payload"]["action"] == "echo.message.send"

    correlation_id = outbound["correlation_id"]
    await agent_communicator.send_json_to(
        build_message(
            message_type="command.started",
            correlation_id=correlation_id,
            payload={"command_id": command_id},
        )
    )
    await agent_communicator.send_json_to(
        build_message(
            message_type="command.progress",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "event": "message.delta",
                "sequence": 1,
                "delta": "Bonjour ",
            },
        )
    )
    await agent_communicator.send_json_to(
        build_message(
            message_type="command.completed",
            correlation_id=correlation_id,
            payload={
                "command_id": command_id,
                "result": {"text": "Bonjour Fabric"},
            },
        )
    )

    messages_payload: list[dict[str, object]] = []
    for _ in range(10):
        listing = await sync_to_async(client.get)(
            f"/api/v1/conversations/{conversation_id}/messages/"
        )
        assert listing.status_code == 200
        messages_payload = listing.json()
        if len(messages_payload) == 2 and messages_payload[-1]["status"] == "succeeded":
            break
        await asyncio.sleep(0.05)

    assert len(messages_payload) == 2
    assert messages_payload[0]["role"] == MessageRole.USER
    assert messages_payload[0]["content"] == "Bonjour Fabric"
    assert messages_payload[1]["id"] == assistant_message_id
    assert messages_payload[1]["status"] == MessageStatus.SUCCEEDED
    assert messages_payload[1]["content"] == "Bonjour Fabric"

    command_event = await event_communicator.receive_json_from()
    assert command_event["type"] == "command.updated"
    assert command_event["payload"]["command"]["status"] in {
        CommandStatus.DISPATCHED,
        CommandStatus.RUNNING,
        CommandStatus.SUCCEEDED,
    }

    await agent_communicator.disconnect()
    await event_communicator.disconnect()
