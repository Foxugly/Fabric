"""Data retention (I4, OPERATIONS.md §3.25 GDPR): what Fabric forgets, and when.

Command output can hold anything the operator's PC printed — a `Get-Content
.env` included — so it is kept only `FABRIC_RETENTION_DAYS` (30 by default).
Run daily by `manage.py purge_fabric_history` (systemd `fabric-purge.timer`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.api_auth.models import WebSocketTicket
from apps.commands.models import (
    Command,
    CommandEvent,
    CommandStatus,
    PermissionRequest,
)
from apps.conversations.models import Conversation, Message

TERMINAL_STATUSES = (
    CommandStatus.SUCCEEDED,
    CommandStatus.FAILED,
    CommandStatus.CANCELLED,
    CommandStatus.TIMED_OUT,
)


@dataclass(frozen=True, slots=True)
class PurgeReport:
    commands: int
    events: int
    permission_requests: int
    messages: int
    conversations: int
    tickets: int

    def __str__(self) -> str:
        return (
            f"commands={self.commands} events={self.events} "
            f"permission_requests={self.permission_requests} "
            f"messages={self.messages} conversations={self.conversations} "
            f"ws_tickets={self.tickets}"
        )


def retention_days() -> int:
    return max(int(getattr(settings, "FABRIC_RETENTION_DAYS", 30)), 1)


def purge_history(*, days: int | None = None) -> PurgeReport:
    """Delete what is older than the retention window, plus expired tickets.

    Only finished commands are deleted: a command still in flight (even an old
    one blocked on an approval) belongs to the reaper, not to the purge.
    """
    now = timezone.now()
    cutoff = now - timedelta(days=days if days is not None else retention_days())

    with transaction.atomic():
        old_commands = Command.objects.filter(status__in=TERMINAL_STATUSES).filter(
            Q(finished_at__lt=cutoff)
            | Q(finished_at__isnull=True, created_at__lt=cutoff)
        )
        # Count the cascade explicitly so the report reflects every row gone.
        events = CommandEvent.objects.filter(
            Q(command__in=old_commands) | Q(created_at__lt=cutoff)
        ).delete()[0]
        permission_requests = PermissionRequest.objects.filter(
            Q(command__in=old_commands)
            | Q(created_at__lt=cutoff) & ~Q(decision="pending")
        ).delete()[0]
        messages = Message.objects.filter(
            Q(command__in=old_commands) | Q(updated_at__lt=cutoff)
        ).delete()[0]
        commands = old_commands.delete()[0]
        # A conversation left empty and untouched is gone too; its title is the
        # first prompt, i.e. content. One that still has commands is kept.
        conversations = (
            Conversation.objects.filter(updated_at__lt=cutoff)
            .filter(messages__isnull=True, commands__isnull=True)
            .delete()[0]
        )
        tickets = WebSocketTicket.objects.filter(expires_at__lte=now).delete()[0]

    return PurgeReport(
        commands=commands,
        events=events,
        permission_requests=permission_requests,
        messages=messages,
        conversations=conversations,
        tickets=tickets,
    )
