from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.agents.models import Agent
from apps.commands.models import Command, CommandStatus
from apps.commands.services import reap_stale_commands


@pytest.fixture
def agent(db: Any) -> Agent:
    owner = get_user_model().objects.create_user(
        username="reaper-owner",
        password="reaper-password",
    )
    return Agent.objects.create(name="reaper-agent", owner=owner)


def _command(agent: Agent, *, started_ago: int, timeout: int = 60) -> Command:
    command = Command.objects.create(
        agent=agent,
        provider="echo",
        action="echo.message.send",
        payload={},
        status=CommandStatus.RUNNING,
        timeout_seconds=timeout,
    )
    Command.objects.filter(id=command.id).update(
        started_at=timezone.now() - timedelta(seconds=started_ago)
    )
    command.refresh_from_db()
    return command


@pytest.mark.django_db
def test_running_command_past_its_timeout_is_timed_out(agent: Agent) -> None:
    command = _command(agent, started_ago=600)

    assert reap_stale_commands() == 1

    command.refresh_from_db()
    assert command.status == CommandStatus.TIMED_OUT
    assert command.finished_at is not None
    assert "No response from the agent" in command.error


@pytest.mark.django_db
def test_running_command_inside_its_budget_is_left_alone(agent: Agent) -> None:
    command = _command(agent, started_ago=5)

    assert reap_stale_commands() == 0

    command.refresh_from_db()
    assert command.status == CommandStatus.RUNNING


@pytest.mark.django_db
def test_finished_commands_are_never_reaped(agent: Agent) -> None:
    command = _command(agent, started_ago=6000)
    command.status = CommandStatus.SUCCEEDED
    command.save(update_fields=["status"])

    assert reap_stale_commands() == 0

    command.refresh_from_db()
    assert command.status == CommandStatus.SUCCEEDED


@pytest.mark.django_db
def test_a_long_timeout_is_honoured(agent: Agent) -> None:
    """A 10-minute Claude turn must not be reaped after the default 60s."""
    command = _command(agent, started_ago=300, timeout=600)

    assert reap_stale_commands() == 0

    command.refresh_from_db()
    assert command.status == CommandStatus.RUNNING
