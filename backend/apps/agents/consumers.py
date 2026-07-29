from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from shared.protocol import ProtocolValidationError, build_message, parse_message

from apps.agents.auth import authenticate_agent
from apps.agents.events import (
    publish_agent_updated,
    publish_command_event,
    publish_command_updated,
)
from apps.agents.models import Agent, AgentStatus
from apps.agents.services import dispatch_pending_commands, update_agent_presence
from apps.api_auth.websocket_auth import authenticate_events_user
from apps.commands.models import Command, CommandEvent, CommandStatus
from apps.conversations.services import (
    sync_message_for_command_completed,
    sync_message_for_command_failed,
    sync_message_for_command_progress,
    sync_message_for_command_started,
)


class EventConsumer(AsyncJsonWebsocketConsumer):
    user_id: int | None = None

    async def connect(self) -> None:
        user = await sync_to_async(authenticate_events_user)(self.scope["query_string"])
        if user is None:
            await self.close(code=4401)
            return

        self.user_id = int(user.id)
        await self.channel_layer.group_add("events.agents", self.channel_name)
        await self.channel_layer.group_add(
            f"events.user.{self.user_id}", self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        await self.channel_layer.group_discard("events.agents", self.channel_name)
        if self.user_id is not None:
            await self.channel_layer.group_discard(
                f"events.user.{self.user_id}",
                self.channel_name,
            )

    async def fabric_event(self, event: dict[str, Any]) -> None:
        await self.send_json(event["message"])


class AgentConsumer(AsyncJsonWebsocketConsumer):
    agent: Agent | None = None

    async def connect(self) -> None:
        agent = await sync_to_async(authenticate_agent)(self.scope["query_string"])
        if agent is None:
            await self.close(code=4401)
            return

        self.agent = agent
        await self.channel_layer.group_add(f"agent.{agent.id}", self.channel_name)
        await self.accept()
        await sync_to_async(update_agent_presence)(
            agent,
            status=AgentStatus.CONNECTING,
        )

        await self.send_json(
            build_message(
                message_type="agent.authenticated",
                correlation_id=str(agent.id),
                payload={"agent_id": str(agent.id)},
            )
        )

    async def disconnect(self, close_code: int) -> None:
        if self.agent is not None:
            await self.channel_layer.group_discard(
                f"agent.{self.agent.id}", self.channel_name
            )
            await sync_to_async(self._mark_offline)()

    async def receive_json(self, content: dict[str, object], **kwargs: object) -> None:
        if self.agent is None:
            await self.close(code=4401)
            return

        try:
            envelope = parse_message(content)
        except ProtocolValidationError as exc:
            await self.send_json(
                build_message(
                    message_type="error",
                    payload={"code": "protocol_error", "message": str(exc)},
                )
            )
            return

        await sync_to_async(self._touch_agent)()
        message_type = envelope.type

        if message_type == "agent.hello":
            await sync_to_async(update_agent_presence)(
                self.agent,
                status=AgentStatus.ONLINE,
                version=str(envelope.payload.get("version", "")),
                operating_system=str(envelope.payload.get("operating_system", "")),
            )
            await sync_to_async(dispatch_pending_commands)(self.agent)
            return

        if message_type == "agent.heartbeat":
            return

        if message_type == "provider.capabilities":
            capabilities = envelope.payload.get("capabilities", [])
            await sync_to_async(self._set_capabilities)(capabilities)
            return

        if message_type == "command.accepted":
            return

        if message_type == "command.started":
            await sync_to_async(self._mark_command_started)(
                str(envelope.payload["command_id"])
            )
            return

        if message_type == "command.progress":
            await sync_to_async(self._append_command_event)(envelope.payload)
            return

        if message_type == "command.completed":
            await sync_to_async(self._mark_command_completed)(envelope.payload)
            return

        if message_type == "command.failed":
            await sync_to_async(self._mark_command_failed)(envelope.payload)

    async def command_request(self, event: dict[str, object]) -> None:
        await self.send_json(event["message"])

    async def command_cancel(self, event: dict[str, object]) -> None:
        await self.send_json(event["message"])

    def _mark_offline(self) -> None:
        if self.agent is None:
            return
        agent = Agent.objects.get(id=self.agent.id)
        agent.mark_offline()
        publish_agent_updated(agent)

    def _touch_agent(self) -> None:
        if self.agent is None:
            return
        agent = Agent.objects.get(id=self.agent.id)
        agent.touch()

    def _set_capabilities(self, capabilities: object) -> None:
        if self.agent is None:
            return
        agent = Agent.objects.get(id=self.agent.id)
        agent.capabilities = (
            list(capabilities) if isinstance(capabilities, list) else []
        )
        agent.save(update_fields=["capabilities", "updated_at"])
        publish_agent_updated(agent)

    def _mark_command_started(self, command_id: str) -> None:
        command = Command.objects.get(id=command_id)
        command.status = CommandStatus.RUNNING
        command.started_at = timezone.now()
        command.save(update_fields=["status", "started_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).update(status=AgentStatus.BUSY)
        command.refresh_from_db()
        sync_message_for_command_started(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)

    def _append_command_event(self, payload: dict[str, object]) -> None:
        command = Command.objects.get(id=str(payload["command_id"]))
        command_event = CommandEvent.objects.create(
            command=command,
            sequence=int(str(payload.get("sequence", 0))),
            event_type=str(payload.get("event", "message.delta")),
            payload=payload,
        )
        sync_message_for_command_progress(command, str(payload.get("delta", "")))
        publish_command_event(command, command_event)

    def _mark_command_completed(self, payload: dict[str, object]) -> None:
        command = Command.objects.get(id=str(payload["command_id"]))
        command.status = CommandStatus.SUCCEEDED
        command.result = payload.get("result", {})
        command.finished_at = timezone.now()
        command.save(update_fields=["status", "result", "finished_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).update(status=AgentStatus.ONLINE)
        command.refresh_from_db()
        sync_message_for_command_completed(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)

    def _mark_command_failed(self, payload: dict[str, object]) -> None:
        command = Command.objects.get(id=str(payload["command_id"]))
        is_cancelled = payload.get("cancelled") is True
        command.status = (
            CommandStatus.CANCELLED if is_cancelled else CommandStatus.FAILED
        )
        command.error = str(payload.get("error", "Unknown error"))
        command.finished_at = timezone.now()
        command.save(update_fields=["status", "error", "finished_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).update(
            status=AgentStatus.ONLINE if is_cancelled else AgentStatus.ERROR
        )
        command.refresh_from_db()
        sync_message_for_command_failed(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)
