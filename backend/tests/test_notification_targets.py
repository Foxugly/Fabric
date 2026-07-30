"""Notification targets are per-user data, not site configuration.

With `Agent.owner` in the picture, "who to notify" belongs to the user: a single
token in SSM would send every operator's approvals to the same phone, and
changing your own preference would need AWS access plus a service restart.
Mirrors `accounts.PushItTarget` in FoxRunner_server.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.agents.models import Agent
from apps.api_auth.models import PushItTarget
from apps.commands import notifications
from apps.commands.models import Command, CommandStatus, PermissionRequest


@pytest.fixture
def owner(db: Any) -> Any:
    return get_user_model().objects.create_user(username="target-owner", password="pw")


@pytest.fixture
def other(db: Any) -> Any:
    return get_user_model().objects.create_user(username="target-other", password="pw")


def _client(user: Any) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _command(user: Any) -> Command:
    agent = Agent.objects.create(name=f"agent-{uuid4().hex[:6]}", owner=user)
    return Command.objects.create(
        requested_by=user,
        agent=agent,
        provider="claude_code_local",
        action="claude_code_local.message.send",
        payload={"text": "hi"},
        status=CommandStatus.RUNNING,
    )


@pytest.mark.django_db
def test_the_first_target_becomes_the_default(owner: Any) -> None:
    target = PushItTarget.objects.create(owner=owner, name="phone", app_token="apt_1")

    assert target.is_default is True


@pytest.mark.django_db
def test_only_one_target_stays_default(owner: Any) -> None:
    first = PushItTarget.objects.create(owner=owner, name="phone", app_token="apt_1")
    second = PushItTarget.objects.create(
        owner=owner, name="tablet", app_token="apt_2", is_default=True
    )

    first.refresh_from_db()
    assert second.is_default is True
    assert first.is_default is False


@pytest.mark.django_db
def test_a_user_only_sees_their_own_targets(owner: Any, other: Any) -> None:
    PushItTarget.objects.create(owner=owner, name="mine", app_token="apt_mine")
    PushItTarget.objects.create(owner=other, name="theirs", app_token="apt_theirs")

    response = _client(owner).get("/api/v1/notification-targets/")

    assert response.status_code == 200
    assert [row["name"] for row in response.json()] == ["mine"]


@pytest.mark.django_db
def test_another_user_cannot_read_or_delete_your_target(
    owner: Any, other: Any
) -> None:
    target = PushItTarget.objects.create(owner=owner, name="mine", app_token="apt_mine")
    client = _client(other)

    url = f"/api/v1/notification-targets/{target.pk}/"
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404
    assert PushItTarget.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_a_created_target_belongs_to_its_creator(owner: Any) -> None:
    response = _client(owner).post(
        "/api/v1/notification-targets/",
        {"name": "phone", "app_token": "apt_created", "title": "Fabric"},
        format="json",
    )

    assert response.status_code == 201
    assert PushItTarget.objects.get(pk=response.json()["id"]).owner_id == owner.pk


@pytest.mark.django_db
def test_an_unknown_event_name_is_refused(owner: Any) -> None:
    """A typo in the policy must fail loudly, not silently do nothing."""
    response = _client(owner).post(
        "/api/v1/notification-targets/",
        {"name": "phone", "app_token": "apt_x", "events": {"permision_request": True}},
        format="json",
    )

    assert response.status_code == 400
    assert "events" in response.json()


@pytest.mark.django_db
def test_the_effective_policy_fills_in_the_defaults(owner: Any) -> None:
    response = _client(owner).post(
        "/api/v1/notification-targets/",
        {
            "name": "phone",
            "app_token": "apt_x",
            "events": {"claude_turn_completed": False},
        },
        format="json",
    )

    effective = response.json()["effective_events"]
    assert effective["claude_turn_completed"] is False
    assert effective["permission_request"] is True


@pytest.mark.django_db
def test_the_notification_follows_the_users_own_target(owner: Any) -> None:
    PushItTarget.objects.create(
        owner=owner,
        name="phone",
        app_token="apt_owner",
        base_url="https://pushit.example.com",
        title="Fabric-Renaud",
    )
    command = _command(owner)
    request = PermissionRequest.objects.create(
        command=command, request_id=uuid4(), tool_name="Bash", tool_input={}
    )

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(command, request)

    destination, payload, _, _ = post.call_args[0]
    assert destination.app_token == "apt_owner"
    assert destination.base_url == "https://pushit.example.com"
    assert json.loads(payload.decode())["title"].startswith("Fabric-Renaud —")


@pytest.mark.django_db
def test_a_disabled_target_mutes_without_losing_the_token(owner: Any) -> None:
    PushItTarget.objects.create(
        owner=owner, name="phone", app_token="apt_owner", enabled=False
    )
    command = _command(owner)

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(
            command,
            PermissionRequest.objects.create(
                command=command, request_id=uuid4(), tool_name="Bash", tool_input={}
            ),
        )

    post.assert_not_called()
    assert PushItTarget.objects.get(owner=owner).app_token == "apt_owner"


@pytest.mark.django_db
def test_the_site_settings_remain_a_fallback(owner: Any, settings: Any) -> None:
    """A deployment with no profile configured must still behave."""
    settings.PUSHIT_ACTIVE = True
    settings.PUSHIT_APP_TOKEN = "apt_site"
    settings.PUSHIT_BASE_URL = "https://pushit-api.example.com"
    settings.PUSHIT_EVENTS = {"permission_request": True}
    command = _command(owner)

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(
            command,
            PermissionRequest.objects.create(
                command=command, request_id=uuid4(), tool_name="Bash", tool_input={}
            ),
        )

    assert post.call_args[0][0].app_token == "apt_site"


@pytest.mark.django_db
def test_no_target_and_no_settings_sends_nothing(owner: Any, settings: Any) -> None:
    settings.PUSHIT_ACTIVE = False
    command = _command(owner)

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(
            command,
            PermissionRequest.objects.create(
                command=command, request_id=uuid4(), tool_name="Bash", tool_input={}
            ),
        )

    post.assert_not_called()
