from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from shared.protocol import build_message

from apps.agents.models import Agent
from apps.agents.serializers import AgentSerializer
from apps.commands.models import Command, CommandEvent, PermissionRequest
from apps.commands.serializers import (
    CommandEventSerializer,
    CommandSerializer,
    PermissionRequestSerializer,
)


def publish_agent_updated(agent: Agent) -> None:
    # Agent presence is only broadcast to its owner: an ownerless agent is
    # staff-only and has nobody to notify.
    if agent.owner_id is None:
        return
    _publish_to_group(
        group_name=f"events.user.{agent.owner_id}",
        message_type="agent.updated",
        correlation_id=str(agent.id),
        payload={"agent": AgentSerializer(agent).data},
    )


def publish_command_updated(command: Command) -> None:
    if command.requested_by_id is None:
        return
    _publish_to_group(
        group_name=f"events.user.{command.requested_by_id}",
        message_type="command.updated",
        correlation_id=str(command.correlation_id),
        payload={"command": CommandSerializer(command).data},
    )


def publish_permission_request(
    command: Command,
    request: PermissionRequest,
) -> None:
    if command.requested_by_id is None:
        return
    _publish_to_group(
        group_name=f"events.user.{command.requested_by_id}",
        message_type="command.permission_request",
        correlation_id=str(command.correlation_id),
        payload={
            "command_id": str(command.id),
            "permission_request": PermissionRequestSerializer(request).data,
        },
    )


def publish_command_event(command: Command, command_event: CommandEvent) -> None:
    if command.requested_by_id is None:
        return
    _publish_to_group(
        group_name=f"events.user.{command.requested_by_id}",
        message_type="command.event",
        correlation_id=str(command.correlation_id),
        payload={
            "command_id": str(command.id),
            "event": CommandEventSerializer(command_event).data,
        },
    )


def _publish_to_group(
    *,
    group_name: str,
    message_type: str,
    payload: dict[str, object],
    correlation_id: str,
) -> None:
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "fabric.event",
            "message": build_message(
                message_type=message_type,
                correlation_id=correlation_id,
                payload=payload,
            ),
        },
    )
