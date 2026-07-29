from __future__ import annotations

from uuid import uuid4

import pytest
from channels.testing.websocket import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from config.asgi import application


@pytest.mark.django_db
def test_login_returns_token(api_user: object) -> None:
    client = APIClient()

    response = client.post(
        "/api/v1/auth/login/",
        {"username": "fabric-admin", "password": "fabric-password"},
        format="json",
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"]
    assert payload["user"]["username"] == "fabric-admin"


@pytest.mark.django_db
def test_agents_endpoint_requires_authentication() -> None:
    client = APIClient()

    response = client.get("/api/v1/agents/")

    assert response.status_code == 401


BROWSER_HEADERS = [(b"origin", b"http://127.0.0.1:4200")]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_rejects_missing_token() -> None:
    communicator = WebsocketCommunicator(
        application, "/ws/v1/events/", headers=BROWSER_HEADERS
    )

    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_rejects_a_foreign_origin() -> None:
    """A malicious page must not open the event feed, even with a valid token."""
    user_model = get_user_model()
    user = await user_model.objects.acreate_user(
        username="origin-user",
        password="origin-password",
    )
    token = await Token.objects.acreate(user=user)

    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/events/?token={token.key}",
        headers=[(b"origin", b"https://evil.example.com")],
    )

    connected, _ = await communicator.connect()

    assert connected is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_does_not_require_an_origin() -> None:
    """The Python agent sends no Origin header, so it must not be origin-checked.

    Only the credential check may refuse it — hence 4401 rather than the origin
    validator's silent denial.
    """
    communicator = WebsocketCommunicator(
        application,
        f"/ws/v1/agent/?agent_id={uuid4()}&token=nope",
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_agent_socket_refuses_a_malformed_agent_id() -> None:
    """A bad UUID must be a refusal, not an unhandled ValidationError."""
    communicator = WebsocketCommunicator(
        application, "/ws/v1/agent/?agent_id=not-a-uuid&token=nope"
    )

    connected, code = await communicator.connect()

    assert connected is False
    assert code == 4401
