"""An agent is remote code execution: only its owner may see or drive it."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.agents.models import Agent


@pytest.fixture
def owner(db: Any) -> Any:
    return get_user_model().objects.create_user(
        username="owner",
        password="owner-password",
    )


@pytest.fixture
def intruder(db: Any) -> Any:
    return get_user_model().objects.create_user(
        username="intruder",
        password="intruder-password",
    )


@pytest.fixture
def owned_agent(owner: Any) -> Agent:
    return Agent.objects.create(name="owned-agent", owner=owner)


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_owner_sees_their_agent(owner: Any, owned_agent: Agent) -> None:
    response = _client(owner).get("/api/v1/agents/")

    assert response.status_code == 200
    assert [entry["id"] for entry in response.json()] == [str(owned_agent.id)]


@pytest.mark.django_db
def test_other_users_cannot_see_the_agent(intruder: Any, owned_agent: Agent) -> None:
    response = _client(intruder).get("/api/v1/agents/")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.django_db
def test_other_users_cannot_mint_a_development_token(
    intruder: Any,
    owned_agent: Agent,
) -> None:
    response = _client(intruder).post(
        f"/api/v1/agents/{owned_agent.id}/development-token/"
    )

    assert response.status_code == 404
    owned_agent.refresh_from_db()
    assert owned_agent.development_token_hash == ""


@pytest.mark.django_db
def test_other_users_cannot_revoke_or_delete_the_agent(
    intruder: Any,
    owned_agent: Agent,
) -> None:
    client = _client(intruder)

    assert client.post(f"/api/v1/agents/{owned_agent.id}/revoke/").status_code == 404
    assert client.delete(f"/api/v1/agents/{owned_agent.id}/").status_code == 404
    assert Agent.objects.filter(id=owned_agent.id).exists()


@pytest.mark.django_db
def test_other_users_cannot_run_a_command_on_the_agent(
    intruder: Any,
    owned_agent: Agent,
) -> None:
    response = _client(intruder).post(
        "/api/v1/commands/",
        {
            "agent_id": str(owned_agent.id),
            "provider": "windows_powershell",
            "action": "windows_powershell.command.run",
            "payload": {"session_id": "s", "command": "whoami"},
        },
        format="json",
    )

    assert response.status_code == 400
    assert "agent_id" in response.json()


@pytest.mark.django_db
def test_other_users_cannot_open_a_conversation_on_the_agent(
    intruder: Any,
    owned_agent: Agent,
) -> None:
    response = _client(intruder).post(
        "/api/v1/conversations/",
        {"agent_id": str(owned_agent.id), "provider": "claude_code_local"},
        format="json",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_created_agents_belong_to_their_creator(owner: Any) -> None:
    response = _client(owner).post(
        "/api/v1/agents/",
        {"name": "new-agent"},
        format="json",
    )

    assert response.status_code == 201
    assert Agent.objects.get(id=response.json()["id"]).owner_id == owner.pk


@pytest.mark.django_db
def test_ownerless_agents_stay_staff_only(intruder: Any) -> None:
    legacy = Agent.objects.create(name="legacy-agent")
    staff = get_user_model().objects.create_user(
        username="staff",
        password="staff-password",
        is_staff=True,
    )

    assert _client(intruder).get("/api/v1/agents/").json() == []
    assert [entry["id"] for entry in _client(staff).get("/api/v1/agents/").json()] == [
        str(legacy.id)
    ]
