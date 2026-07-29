from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone
from shared.protocol import build_message

from apps.agents.events import publish_agent_updated, publish_command_updated
from apps.agents.models import Agent, AgentStatus
from apps.commands.models import Command, CommandStatus


def dispatch_pending_commands(agent: Agent) -> None:
    pending_commands = list(
        Command.objects.filter(
            agent=agent,
            status=CommandStatus.PENDING,
        ).order_by("created_at")
    )
    for command in pending_commands:
        dispatch_command(command)


def dispatch_command(command: Command) -> None:
    if command.agent.status not in {
        AgentStatus.ONLINE,
        AgentStatus.BUSY,
        AgentStatus.ERROR,
    }:
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    command.status = CommandStatus.DISPATCHED
    command.save(update_fields=["status", "updated_at"])
    publish_command_updated(command)

    async_to_sync(channel_layer.group_send)(
        f"agent.{command.agent_id}",
        {
            "type": "command.request",
            "message": build_message(
                message_type="command.request",
                correlation_id=str(command.correlation_id),
                payload={
                    "command_id": str(command.id),
                    "provider": command.provider,
                    "action": command.action,
                    "parameters": command.payload,
                    "timeout_seconds": command.timeout_seconds,
                },
            ),
        },
    )


def dispatch_command_cancel(command: Command) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        f"agent.{command.agent_id}",
        {
            "type": "command.cancel",
            "message": build_message(
                message_type="command.cancel",
                correlation_id=str(command.correlation_id),
                payload={
                    "command_id": str(command.id),
                    "provider": command.provider,
                    "action": command.action,
                    "parameters": command.payload,
                },
            ),
        },
    )


def dispatch_permission_decision(
    command: Command,
    *,
    request_id: str,
    allowed: bool,
    message: str = "",
) -> None:
    """Unblock the Claude Code turn waiting on this approval."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        f"agent.{command.agent_id}",
        {
            "type": "session.action_response",
            "message": build_message(
                message_type="session.action_response",
                correlation_id=str(command.correlation_id),
                payload={
                    "command_id": str(command.id),
                    "request_id": request_id,
                    "behavior": "allow" if allowed else "deny",
                    "message": message,
                },
            ),
        },
    )


def update_agent_presence(
    agent: Agent,
    *,
    status: str | None = None,
    version: str = "",
    operating_system: str = "",
) -> None:
    now = timezone.now()
    agent.status = status or AgentStatus.ONLINE
    agent.version = version or agent.version
    agent.operating_system = operating_system or agent.operating_system
    agent.last_connected_at = now
    agent.last_activity_at = now
    agent.save(
        update_fields=[
            "status",
            "version",
            "operating_system",
            "last_connected_at",
            "last_activity_at",
            "updated_at",
        ]
    )
    publish_agent_updated(agent)
