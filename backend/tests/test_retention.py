"""I4: the daily purge forgets old output and expired credentials."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO
from typing import Any
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.agents.models import Agent
from apps.api_auth.models import WebSocketTicket
from apps.commands.models import (
    Command,
    CommandEvent,
    CommandStatus,
    PermissionRequest,
)
from apps.conversations.models import Conversation, Message, MessageRole


@pytest.fixture
def owner(db: Any) -> Any:
    return get_user_model().objects.create_user(username="retention", password="pw")


def _age(model: Any, pk: Any, days: int, **fields: Any) -> None:
    """Backdate a row: auto_now fields ignore values passed to save()."""
    when = timezone.now() - timedelta(days=days)
    names = {
        "created_at",
        "updated_at",
        "finished_at",
        "last_accessed_at",
    } & {field.name for field in model._meta.get_fields()}
    model.objects.filter(pk=pk).update(**{name: when for name in names}, **fields)


def _command(owner: Any, agent: Agent, status: str, **kwargs: Any) -> Command:
    return Command.objects.create(
        requested_by=owner,
        agent=agent,
        provider="windows_powershell",
        action="windows_powershell.command.execute",
        payload={"command": "Get-Content .env"},
        status=status,
        result={"stdout": "SECRET=1"},
        **kwargs,
    )


@pytest.mark.django_db
def test_the_purge_deletes_old_output_and_keeps_recent_or_live_work(
    owner: Any, settings: Any
) -> None:
    settings.FABRIC_RETENTION_DAYS = 30
    agent = Agent.objects.create(name="pc", owner=owner)
    conversation = Conversation.objects.create(
        owner=owner, agent=agent, provider="claude_code_local", title="old chat"
    )

    old = _command(owner, agent, CommandStatus.SUCCEEDED, conversation=conversation)
    CommandEvent.objects.create(command=old, sequence=1, event_type="d", payload={})
    PermissionRequest.objects.create(
        command=old, request_id=uuid4(), tool_name="Bash", decision="allowed"
    )
    Message.objects.create(
        conversation=conversation, command=old, role=MessageRole.ASSISTANT,
        content="SECRET=1",
    )
    _age(Command, old.pk, 31)
    for message in Message.objects.filter(command=old):
        _age(Message, message.pk, 31)
    _age(Conversation, conversation.pk, 31)

    recent = _command(owner, agent, CommandStatus.SUCCEEDED)
    _age(Command, recent.pk, 5)
    # Old but still blocked on an approval: the purge must not pull it away.
    live = _command(owner, agent, CommandStatus.WAITING_USER_ACTION)
    _age(Command, live.pk, 40, status=CommandStatus.WAITING_USER_ACTION)
    pending = PermissionRequest.objects.create(
        command=live, request_id=uuid4(), tool_name="Bash"
    )
    _age(PermissionRequest, pending.pk, 40)

    WebSocketTicket.objects.create(
        key_hash="a" * 64, user=owner, expires_at=timezone.now() - timedelta(seconds=1)
    )
    fresh_ticket = WebSocketTicket.objects.create(
        key_hash="b" * 64, user=owner, expires_at=timezone.now() + timedelta(seconds=30)
    )

    out = StringIO()
    call_command("purge_fabric_history", stdout=out)

    assert not Command.objects.filter(pk=old.pk).exists()
    assert not CommandEvent.objects.filter(command_id=old.pk).exists()
    assert not PermissionRequest.objects.filter(command_id=old.pk).exists()
    assert not Message.objects.filter(content="SECRET=1").exists()
    assert not Conversation.objects.filter(pk=conversation.pk).exists()
    assert Command.objects.filter(pk=recent.pk).exists()
    assert Command.objects.filter(pk=live.pk).exists()
    assert PermissionRequest.objects.filter(pk=pending.pk).exists()
    assert list(WebSocketTicket.objects.values_list("pk", flat=True)) == [
        fresh_ticket.pk
    ]
    assert "commands=1" in out.getvalue()
    assert "ws_tickets=1" in out.getvalue()


@pytest.mark.django_db
def test_the_window_is_configurable(owner: Any, settings: Any) -> None:
    agent = Agent.objects.create(name="pc", owner=owner)
    command = _command(owner, agent, CommandStatus.FAILED)
    _age(Command, command.pk, 10)

    settings.FABRIC_RETENTION_DAYS = 30
    call_command("purge_fabric_history", stdout=StringIO())
    assert Command.objects.filter(pk=command.pk).exists()

    call_command("purge_fabric_history", "--days", "7", stdout=StringIO())
    assert not Command.objects.filter(pk=command.pk).exists()


@pytest.mark.django_db
def test_the_purge_flushes_expired_refresh_tokens(owner: Any) -> None:
    RefreshToken.for_user(owner)
    live = RefreshToken.for_user(owner)
    OutstandingToken.objects.exclude(jti=live["jti"]).update(
        expires_at=timezone.now() - timedelta(days=1)
    )

    call_command("purge_fabric_history", stdout=StringIO())

    assert list(OutstandingToken.objects.values_list("jti", flat=True)) == [
        live["jti"]
    ]
