from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.agents.events import publish_command_updated
from apps.commands.models import Command, CommandStatus
from apps.conversations.services import sync_message_for_command_failed

LOGGER = logging.getLogger(__name__)

ACTIVE_STATUSES = (
    CommandStatus.DISPATCHED,
    CommandStatus.RUNNING,
    CommandStatus.WAITING_USER_ACTION,
)


def reap_stale_commands(*, agent_id: str | None = None) -> int:
    """Time out commands whose agent stopped reporting.

    Fabric has no scheduler, so this runs opportunistically whenever commands
    are read. Without it a command whose agent was killed mid-turn stays
    `running` forever and the terminal stays locked on it.

    The deadline is per-command (`timeout_seconds` plus a grace period) and the
    set of active commands is tiny, so filtering in Python keeps this portable
    across SQLite and PostgreSQL.
    """
    grace = timedelta(seconds=settings.FABRIC_COMMAND_GRACE_SECONDS)
    now = timezone.now()

    active = Command.objects.filter(status__in=ACTIVE_STATUSES)
    if agent_id is not None:
        active = active.filter(agent_id=agent_id)

    reaped = 0
    for command in list(active):
        started_at = command.started_at or command.created_at
        deadline = started_at + timedelta(seconds=command.timeout_seconds) + grace
        if now <= deadline:
            continue

        command.status = CommandStatus.TIMED_OUT
        command.error = (
            f"No response from the agent within {command.timeout_seconds}s "
            f"(+{settings.FABRIC_COMMAND_GRACE_SECONDS}s grace)"
        )
        command.finished_at = now
        command.save(update_fields=["status", "error", "finished_at", "updated_at"])
        sync_message_for_command_failed(command)
        publish_command_updated(command)
        reaped += 1

    if reaped:
        LOGGER.info("Timed out %s stale command(s)", reaped)
    return reaped
