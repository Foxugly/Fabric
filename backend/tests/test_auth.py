from __future__ import annotations

import pytest
from channels.testing.websocket import WebsocketCommunicator
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


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_frontend_events_socket_rejects_missing_token() -> None:
    communicator = WebsocketCommunicator(application, "/ws/v1/events/")

    connected, _ = await communicator.connect()

    assert connected is False
