"""GDPR (§3.25): `DELETE /api/v1/auth/me/` erases the caller and what they own."""

from __future__ import annotations

from typing import Any, cast

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken
from ws_helpers import agent_headers

from apps.agents.models import Agent
from apps.api_auth.models import PushItTarget
from apps.commands.models import Command, CommandEvent, CommandStatus
from apps.conversations.models import Conversation, Message, MessageRole
from config.asgi import application

URL = "/api/v1/auth/me/"
PASSWORD = "delete-me-password"


def _populate(username: str) -> tuple[Any, Agent, Command]:
    user = get_user_model().objects.create_user(username=username, password=PASSWORD)
    agent = Agent.objects.create(name=f"{username}-pc", owner=user)
    conversation = Conversation.objects.create(
        owner=user, agent=agent, provider="claude_code_local"
    )
    command = Command.objects.create(
        requested_by=user,
        agent=agent,
        conversation=conversation,
        provider="windows_powershell",
        action="windows_powershell.command.execute",
        payload={"command": "env"},
        status=CommandStatus.SUCCEEDED,
        result={"stdout": "SECRET=1"},
    )
    CommandEvent.objects.create(command=command, sequence=1, event_type="d")
    Message.objects.create(
        conversation=conversation,
        command=command,
        role=MessageRole.ASSISTANT,
        content="SECRET=1",
    )
    PushItTarget.objects.create(owner=user, name="phone", app_token="apt_1")
    return user, agent, command


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_deleting_your_account_erases_your_data_only() -> None:
    user, agent, command = _populate("leaving")
    other, other_agent, other_command = _populate("staying")
    refresh = RefreshToken.for_user(user)

    response = _client(user).delete(URL, {"password": PASSWORD}, format="json")

    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=user.pk).exists()
    assert not Agent.objects.filter(pk=agent.pk).exists()
    assert not Command.objects.filter(pk=command.pk).exists()
    assert not CommandEvent.objects.filter(command_id=command.pk).exists()
    assert not Conversation.objects.filter(owner_id=user.pk).exists()
    assert not Message.objects.filter(content="SECRET=1", command=None).exists()
    assert not PushItTarget.objects.filter(owner_id=user.pk).exists()
    # The refresh token cannot outlive the account.
    assert BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()

    assert Agent.objects.filter(pk=other_agent.pk).exists()
    assert Command.objects.filter(pk=other_command.pk).exists()
    assert PushItTarget.objects.filter(owner=other).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("body", [{}, {"password": "wrong-password"}])
def test_deletion_requires_the_password(body: dict[str, str]) -> None:
    user, agent, _command = _populate("careful")

    response = _client(user).delete(URL, body, format="json")

    assert response.status_code == 400
    assert response.json()["code"] == "password_invalid"
    assert get_user_model().objects.filter(pk=user.pk).exists()
    assert Agent.objects.filter(pk=agent.pk).exists()


@pytest.mark.django_db
def test_deletion_needs_authentication() -> None:
    response = APIClient().delete(URL, {"password": PASSWORD}, format="json")

    assert response.status_code == 401


@pytest.mark.django_db
def test_deletion_is_throttled() -> None:
    """The password check must not be an unbounded oracle. DRF freezes the
    rates at import time, so this exercises the configured one."""
    from django.conf import settings

    rates = cast(dict[str, str], settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
    rate = rates["account_delete"]
    limit = int(rate.split("/")[0])
    user, _agent, _command = _populate("guesser")
    client = _client(user)

    codes = [
        client.delete(URL, {"password": "nope"}, format="json").status_code
        for _ in range(limit + 1)
    ]

    assert codes == [400] * limit + [429]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_deletion_closes_the_agents_open_sockets() -> None:
    user, agent, _command = await sync_to_async(_populate)("socket-owner")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(token)
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.receive_json_from()

    response = await sync_to_async(_client(user).delete)(
        URL, {"password": PASSWORD}, format="json"
    )
    assert response.status_code == 204

    output = await communicator.receive_output(timeout=2)
    assert output["type"] == "websocket.close", output
    assert output.get("code") == 4401
