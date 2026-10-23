"""I1: revoking or rotating an agent token takes effect on open sockets at once."""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from shared.protocol import build_message
from ws_helpers import agent_headers

from apps.agents.models import Agent, AgentStatus
from config.asgi import application


async def _owner_and_agent() -> tuple[Any, Agent, str]:
    owner = await get_user_model().objects.acreate_user(
        username="owner", password="owner-password"
    )
    agent = await Agent.objects.acreate(name="pc", owner=owner)
    token = await sync_to_async(agent.issue_development_token)()
    return owner, agent, token


async def _connect(token: str) -> WebsocketCommunicator:
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(token)
    )
    connected, _ = await communicator.connect()
    assert connected is True
    authenticated = await communicator.receive_json_from()
    assert authenticated["type"] == "agent.authenticated"
    return communicator


def _hello(agent: Agent) -> dict[str, Any]:
    return build_message(
        message_type="agent.hello",
        correlation_id=str(agent.id),
        payload={"version": "9.9.9", "operating_system": "Windows"},
    )


def _post_as(user: Any, path: str) -> int:
    client = APIClient()
    client.force_authenticate(user=user)
    return int(client.post(path).status_code)


async def _expect_close(communicator: WebsocketCommunicator, code: int) -> None:
    output = await communicator.receive_output(timeout=2)
    assert output["type"] == "websocket.close", output
    assert output.get("code") == code, output


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_revoking_closes_the_open_socket_and_keeps_the_agent_disabled() -> None:
    owner, agent, token = await _owner_and_agent()
    communicator = await _connect(token)
    await communicator.send_json_to(_hello(agent))
    # Let the hello be processed before revoking.
    await communicator.receive_nothing(timeout=0.2)

    status = await sync_to_async(_post_as)(owner, f"/api/v1/agents/{agent.id}/revoke/")

    assert status == 200
    await _expect_close(communicator, 4401)
    await communicator.disconnect()
    refreshed = await Agent.objects.aget(id=agent.id)
    # The socket closing must not turn DISABLED back into OFFLINE.
    assert refreshed.status == AgentStatus.DISABLED

    # And the revoked token cannot reconnect.
    again = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(token)
    )
    connected, code = await again.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_hello_cannot_bring_a_revoked_agent_back_online() -> None:
    """Even if the close event is lost, the next message is refused."""
    _, agent, token = await _owner_and_agent()
    communicator = await _connect(token)

    # Revoke behind the consumer's back: no channel-layer message.
    await Agent.objects.filter(id=agent.id).aupdate(
        status=AgentStatus.DISABLED, development_token_hash=""
    )
    await communicator.send_json_to(_hello(agent))

    await _expect_close(communicator, 4401)
    await communicator.disconnect()
    refreshed = await Agent.objects.aget(id=agent.id)
    assert refreshed.status == AgentStatus.DISABLED


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_rotating_the_token_closes_the_socket_opened_with_the_old_one() -> None:
    owner, agent, old_token = await _owner_and_agent()
    communicator = await _connect(old_token)

    client = APIClient()
    client.force_authenticate(user=owner)
    response = await sync_to_async(client.post)(
        f"/api/v1/agents/{agent.id}/development-token/"
    )
    assert response.status_code == 200

    await _expect_close(communicator, 4401)
    await communicator.disconnect()

    # The new token works, the old one does not.
    fresh = await _connect(response.json()["development_token"])
    await fresh.disconnect()
    stale = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(old_token)
    )
    connected, _ = await stale.connect()
    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_new_socket_evicts_the_previous_one() -> None:
    """Only one live socket per agent: the newest wins, the old one is closed."""
    _, agent, token = await _owner_and_agent()
    first = await _connect(token)
    second = await _connect(token)

    await _expect_close(first, 4409)
    await first.disconnect()

    # The evicted socket's disconnect must not mark the live agent offline.
    await second.send_json_to(_hello(agent))
    await second.receive_nothing(timeout=0.2)
    refreshed = await Agent.objects.aget(id=agent.id)
    assert refreshed.status == AgentStatus.ONLINE
    assert refreshed.active_channel

    await second.disconnect()
    refreshed = await Agent.objects.aget(id=agent.id)
    assert refreshed.status == AgentStatus.OFFLINE
    assert refreshed.active_channel == ""


@pytest.mark.django_db
def test_issuing_a_token_is_the_explicit_way_back_from_disabled() -> None:
    agent = Agent.objects.create(name="pc", status=AgentStatus.DISABLED)

    agent.issue_development_token()
    agent.refresh_from_db()

    assert agent.status == AgentStatus.OFFLINE


@pytest.mark.django_db
def test_presence_update_never_leaves_disabled() -> None:
    from apps.agents.services import update_agent_presence

    agent = Agent.objects.create(name="pc", status=AgentStatus.DISABLED)

    assert update_agent_presence(agent, status=AgentStatus.ONLINE) is False
    agent.refresh_from_db()
    assert agent.status == AgentStatus.DISABLED
