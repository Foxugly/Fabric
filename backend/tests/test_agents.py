from __future__ import annotations

from typing import Any, cast

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from shared.protocol import build_message

from apps.agents.models import Agent, AgentStatus
from config.asgi import application

#: The browser event socket is origin-checked; a real browser always sends this.
BROWSER_HEADERS = [(b"origin", b"http://127.0.0.1:4200")]


async def _receive_event_type(
    communicator: WebsocketCommunicator,
    expected_type: str,
) -> dict[str, Any]:
    for _ in range(5):
        event = cast(dict[str, Any], await communicator.receive_json_from())
        if event["type"] == expected_type:
            return event
    raise AssertionError(f"Did not receive event type {expected_type}")


@pytest.mark.django_db
def test_agent_issues_hashed_development_token() -> None:
    agent = Agent.objects.create(name="test-agent")
    token = agent.issue_development_token()

    assert token
    assert agent.development_token_hash
    assert agent.check_development_token(token) is True
    assert agent.check_development_token("wrong-token") is False


@pytest.mark.django_db
def test_agent_touch_recovers_connected_agent_from_error_status() -> None:
    agent = Agent.objects.create(name="recover-agent", status=AgentStatus.ERROR)

    agent.touch()
    agent.refresh_from_db()

    assert agent.status == AgentStatus.ONLINE
    assert agent.last_activity_at is not None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_websocket_authenticates_and_updates_status() -> None:
    agent = await Agent.objects.acreate(name="test-agent")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={token}",
    )

    connected, _ = await communicator.connect()
    assert connected is True

    response = await communicator.receive_json_from()
    assert response["type"] == "agent.authenticated"

    await communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.1.0", "operating_system": "Windows"},
        )
    )

    await communicator.disconnect()
    refreshed = await Agent.objects.aget(id=agent.id)
    assert refreshed.status == AgentStatus.OFFLINE


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_receives_agent_updates() -> None:
    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="events-user",
        password="events-password",
    )
    token = await Token.objects.acreate(user=user)

    event_communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/events/?token={token.key}",
        headers=BROWSER_HEADERS,
    )
    connected, _ = await event_communicator.connect()
    assert connected is True

    agent = await Agent.objects.acreate(name="events-agent", owner=user)
    agent_token = await sync_to_async(agent.issue_development_token)()
    agent_communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={agent.id}&token={agent_token}",
    )
    agent_connected, _ = await agent_communicator.connect()
    assert agent_connected is True
    await agent_communicator.receive_json_from()

    await agent_communicator.send_json_to(
        build_message(
            message_type="agent.hello",
            correlation_id=str(agent.id),
            payload={"version": "0.2.0", "operating_system": "Windows 11"},
        )
    )

    event = await _receive_event_type(event_communicator, "agent.updated")
    if event["payload"]["agent"]["status"] != AgentStatus.ONLINE:
        event = await _receive_event_type(event_communicator, "agent.updated")
    assert event["type"] == "agent.updated"
    assert event["payload"]["agent"]["id"] == str(agent.id)
    assert event["payload"]["agent"]["status"] == AgentStatus.ONLINE

    await agent_communicator.disconnect()
    await event_communicator.disconnect()
