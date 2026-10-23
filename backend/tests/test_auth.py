from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from mfa_helpers import enroll, login
from rest_framework.test import APIClient
from ws_helpers import BROWSER_HEADERS, agent_headers, events_path

from apps.agents.models import Agent
from apps.api_auth.models import WebSocketTicket
from apps.api_auth.tickets import issue_ticket
from config.asgi import application


@pytest.mark.django_db
def test_login_returns_jwt_pair_after_the_totp_step(api_user: object) -> None:
    client = APIClient()
    secret = enroll(api_user)

    response = login(
        client, {"username": "fabric-admin", "password": "fabric-password"}, secret
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access"]
    assert payload["refresh"]
    assert "token" not in payload
    assert payload["user"]["username"] == "fabric-admin"


@pytest.mark.django_db
def test_agents_endpoint_requires_authentication() -> None:
    client = APIClient()

    response = client.get("/api/v1/agents/")

    assert response.status_code == 401


@pytest.mark.django_db
def test_drf_token_scheme_is_gone(api_user: Any) -> None:
    """The legacy `Authorization: Token …` scheme authenticates nothing now."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION="Token 0123456789abcdef")

    assert client.get("/api/v1/agents/").status_code == 401


# --- Browser event socket: single-use ticket ---------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_rejects_missing_ticket() -> None:
    communicator = WebsocketCommunicator(
        application, "/ws/v1/events/", headers=BROWSER_HEADERS
    )

    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_rejects_a_foreign_origin() -> None:
    """A malicious page must not open the event feed, even with a valid ticket."""
    user = await get_user_model().objects.acreate_user(
        username="origin-user",
        password="origin-password",
    )

    communicator = WebsocketCommunicator(
        application,
        await events_path(user),
        headers=[(b"origin", b"https://evil.example.com")],
    )

    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_ticket_works_exactly_once() -> None:
    user = await get_user_model().objects.acreate_user(
        username="ticket-user", password="ticket-password"
    )
    path = await events_path(user)

    first = WebsocketCommunicator(application, path, headers=BROWSER_HEADERS)
    connected, _ = await first.connect()
    assert connected is True
    await first.disconnect()

    replay = WebsocketCommunicator(application, path, headers=BROWSER_HEADERS)
    connected, code = await replay.connect()
    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_expired_ticket_is_refused() -> None:
    from datetime import timedelta

    from django.utils import timezone

    user = await get_user_model().objects.acreate_user(
        username="late-user", password="late-password"
    )
    ticket = await sync_to_async(issue_ticket)(user)
    await WebSocketTicket.objects.aupdate(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    communicator = WebsocketCommunicator(
        application, f"/ws/v1/events/?ticket={ticket}", headers=BROWSER_HEADERS
    )
    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_ticket_of_an_inactive_user_is_refused() -> None:
    user = await get_user_model().objects.acreate_user(
        username="gone-user", password="gone-password"
    )
    path = await events_path(user)
    user.is_active = False
    await user.asave(update_fields=["is_active"])

    communicator = WebsocketCommunicator(application, path, headers=BROWSER_HEADERS)
    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.django_db
def test_ws_ticket_endpoint_requires_authentication() -> None:
    assert APIClient().post("/api/v1/auth/ws-ticket/").status_code == 401


@pytest.mark.django_db
def test_ws_ticket_endpoint_issues_a_short_lived_hashed_ticket(api_user: Any) -> None:
    client = APIClient()
    client.force_authenticate(user=api_user)

    response = client.post("/api/v1/auth/ws-ticket/")

    assert response.status_code == 201
    body = response.json()
    assert body["ticket"]
    assert 0 < body["expires_in"] <= 30
    stored = WebSocketTicket.objects.get(user=api_user)
    # Only the hash is stored.
    assert stored.key_hash != body["ticket"]


# --- Agent socket: Authorization header only ---------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_does_not_require_an_origin() -> None:
    """The Python agent sends no Origin header, so it must not be origin-checked.

    Only the credential check may refuse it — hence 4401 rather than the origin
    validator's silent denial.
    """
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers("nope")
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_refuses_a_malformed_agent_id() -> None:
    """A bad UUID must be a refusal, not an unhandled ValidationError."""
    agent = await Agent.objects.acreate(name="malformed")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        "/ws/v1/agent/?agent_id=not-a-uuid",
        headers=agent_headers(token),
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_refuses_a_token_in_the_query_string() -> None:
    """The URL ends up in access logs: a token there must not authenticate."""
    agent = await Agent.objects.acreate(name="legacy-url")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application, f"/ws/v1/agent/?agent_id={agent.id}&token={token}"
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_accepts_the_bearer_header() -> None:
    agent = await Agent.objects.acreate(name="bearer")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/", headers=agent_headers(token)
    )

    connected, _ = await communicator.connect()

    assert connected is True
    await communicator.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_refuses_an_agent_id_naming_another_agent() -> None:
    agent = await Agent.objects.acreate(name="mine")
    token = await sync_to_async(agent.issue_development_token)()
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={uuid4()}",
        headers=agent_headers(token),
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401
