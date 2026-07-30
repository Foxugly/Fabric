"""PushIT notifications: opt-in, policy-driven, and never able to break a command."""

from __future__ import annotations

import json
import threading
import urllib.request
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from apps.agents.models import Agent
from apps.commands import notifications
from apps.commands.models import Command, CommandStatus, PermissionRequest

TOKEN = "apt_test-token"


@pytest.fixture
def command(db: Any) -> Command:
    owner = get_user_model().objects.create_user(username="notif", password="pw")
    agent = Agent.objects.create(name="notif-agent", owner=owner)
    return Command.objects.create(
        requested_by=owner,
        agent=agent,
        provider="claude_code_local",
        action="claude_code_local.message.send",
        payload={"text": "push the branch"},
        status=CommandStatus.RUNNING,
    )


@pytest.fixture
def active(settings: Any) -> None:
    settings.PUSHIT_ENABLED = True
    settings.PUSHIT_APP_TOKEN = TOKEN
    settings.PUSHIT_BASE_URL = "https://pushit-api.example.com"
    settings.PUSHIT_ACTIVE = True
    settings.PUSHIT_TIMEOUT_SECONDS = 5
    settings.PUSHIT_EVENTS = {
        "permission_request": True,
        "claude_turn_completed": True,
        "claude_turn_failed": True,
        "agent_offline": True,
    }


def _permission(command: Command, **kwargs: Any) -> PermissionRequest:
    return PermissionRequest.objects.create(
        command=command,
        request_id=uuid4(),
        tool_name=kwargs.get("tool_name", "Bash"),
        tool_input=kwargs.get("tool_input", {"command": "git push --force"}),
    )


@pytest.mark.django_db
def test_a_token_alone_sends_nothing(command: Command, settings: Any) -> None:
    """Credentials are not consent — the switch must be on too."""
    settings.PUSHIT_APP_TOKEN = TOKEN
    settings.PUSHIT_ENABLED = False
    settings.PUSHIT_ACTIVE = False

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(command, _permission(command))

    post.assert_not_called()


@pytest.mark.django_db
def test_an_event_switched_off_sends_nothing(
    command: Command, active: None, settings: Any
) -> None:
    settings.PUSHIT_EVENTS = {**settings.PUSHIT_EVENTS, "permission_request": False}

    with patch.object(notifications, "_post") as post:
        notifications.notify_permission_request(command, _permission(command))

    post.assert_not_called()


@pytest.mark.django_db
def test_permission_request_carries_the_decidable_detail(
    command: Command, active: None
) -> None:
    """A notification you cannot act on is worthless: it must name the tool."""
    with patch.object(notifications, "_post") as post:
        request = _permission(command)
        notifications.notify_permission_request(command, request)

    _destination, payload, key, event = post.call_args[0]
    body = json.loads(payload.decode("utf-8"))
    assert event == "permission_request"
    assert body["title"].startswith("Fabric — ")
    assert key == f"fabric:perm:{request.request_id}"
    assert "Bash" in body["message"]
    assert "git push --force" in body["message"]


@pytest.mark.django_db
def test_powershell_commands_do_not_notify(command: Command, active: None) -> None:
    """A `git status` finishing must not buzz a phone."""
    command.provider = "windows_powershell"
    command.status = CommandStatus.SUCCEEDED
    command.save(update_fields=["provider", "status"])

    with patch.object(notifications, "_post") as post:
        notifications.notify_command_finished(command)

    post.assert_not_called()


@pytest.mark.django_db
def test_a_finished_claude_turn_notifies_with_an_excerpt(
    command: Command, active: None
) -> None:
    command.status = CommandStatus.SUCCEEDED
    command.result = {"text": "Branche poussée sur origin/main."}
    command.save(update_fields=["status", "result"])

    with patch.object(notifications, "_post") as post:
        notifications.notify_command_finished(command)

    _destination, payload, key, event = post.call_args[0]
    body = json.loads(payload.decode("utf-8"))
    assert event == "claude_turn_completed"
    assert key.endswith(f":{CommandStatus.SUCCEEDED}")
    assert "origin/main" in body["message"]


@pytest.mark.django_db
def test_a_failed_turn_reports_the_error(command: Command, active: None) -> None:
    command.status = CommandStatus.FAILED
    command.error = "Claude Code CLI timed out"
    command.save(update_fields=["status", "error"])

    with patch.object(notifications, "_post") as post:
        notifications.notify_command_finished(command)

    _destination, payload, _, event = post.call_args[0]
    assert event == "claude_turn_failed"
    assert "timed out" in json.loads(payload.decode("utf-8"))["message"]


@pytest.mark.django_db
def test_long_messages_are_truncated(command: Command, active: None) -> None:
    command.status = CommandStatus.SUCCEEDED
    command.result = {"text": "x" * 5000}
    command.save(update_fields=["status", "result"])

    with patch.object(notifications, "_post") as post:
        notifications.notify_command_finished(command)

    body = json.loads(post.call_args[0][1].decode("utf-8"))
    assert len(body["message"]) <= notifications.MESSAGE_MAX_CHARS
    assert body["message"].endswith("…")


@pytest.mark.django_db
def test_pushit_being_down_never_breaks_the_caller(
    command: Command, active: None
) -> None:
    """The whole contract: a notification failure is invisible to the command."""
    command.status = CommandStatus.SUCCEEDED
    command.save(update_fields=["status"])

    with patch.object(urllib.request, "urlopen", side_effect=OSError("network down")):
        notifications.notify_command_finished(command)
        # `_post` runs on a daemon thread; drain it so the failure would surface
        # here if it were ever allowed to propagate.
        for thread in list(threading.enumerate()):
            if thread.name.startswith("pushit-"):
                thread.join(timeout=5)


def test_the_event_policy_reads_json(settings: Any) -> None:
    """The policy is JSON so it can change without a deploy."""
    import importlib

    with patch.dict(
        "os.environ",
        {"PUSHIT_EVENTS": '{"permission_request": false, "agent_offline": true}'},
    ):
        module = importlib.reload(importlib.import_module("config.settings"))
        try:
            assert module.PUSHIT_EVENTS["permission_request"] is False
            assert module.PUSHIT_EVENTS["agent_offline"] is True
            # Unmentioned keys keep their default.
            assert module.PUSHIT_EVENTS["claude_turn_completed"] is True
        finally:
            importlib.reload(importlib.import_module("config.settings"))


def test_a_malformed_policy_falls_back_to_the_defaults() -> None:
    """Bad JSON must not silently mute every notification."""
    import importlib

    with patch.dict("os.environ", {"PUSHIT_EVENTS": "{not json"}):
        module = importlib.reload(importlib.import_module("config.settings"))
        try:
            assert module.PUSHIT_EVENTS == module.PUSHIT_DEFAULT_EVENTS
        finally:
            importlib.reload(importlib.import_module("config.settings"))
