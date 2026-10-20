from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from shared.protocol import ProtocolValidationError, build_message, parse_message

from apps.agents.auth import authenticate_agent
from apps.agents.events import (
    publish_agent_updated,
    publish_command_event,
    publish_command_updated,
    publish_permission_request,
)
from apps.agents.models import Agent, AgentStatus
from apps.agents.services import dispatch_pending_commands, update_agent_presence
from apps.api_auth.websocket_auth import authenticate_events_user
from apps.commands.models import (
    Command,
    CommandEvent,
    CommandStatus,
    PermissionRequest,
)
from apps.commands.notifications import (
    notify_agent_offline,
    notify_command_finished,
    notify_permission_request,
)
from apps.commands.output_cap import (
    ERROR_MAX_CHARS,
    TOOL_INPUT_MAX_CHARS,
    cap_strings,
    cap_text,
    output_max_chars,
)
from apps.conversations.services import (
    sync_message_for_command_completed,
    sync_message_for_command_failed,
    sync_message_for_command_progress,
    sync_message_for_command_started,
)

LOGGER = logging.getLogger(__name__)

_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


def _fingerprint(value: object) -> str:
    """The agent's SHA-256 of the request, kept only if it looks like one."""
    if isinstance(value, str) and _FINGERPRINT.fullmatch(value):
        return value
    return ""


#: Close codes: 1008 policy violation (rate), 1009 message too big.
CLOSE_RATE_LIMITED = 1008
CLOSE_TOO_BIG = 1009

#: Statuses a command can still receive output in.
_ACTIVE_STATUSES = {
    CommandStatus.DISPATCHED,
    CommandStatus.RUNNING,
    CommandStatus.WAITING_USER_ACTION,
}

_MAX_SEQUENCE = 2_147_483_647

_COMMAND_MESSAGES = {
    "command.accepted",
    "command.started",
    "command.progress",
    "session.action_required",
    "command.completed",
    "command.failed",
}


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _optional(payload: dict[str, Any], key: str, kind: type | tuple[type, ...]) -> bool:
    value = payload.get(key)
    return value is None or isinstance(value, kind)


def _payload_error(message_type: str, payload: dict[str, Any]) -> str | None:
    """Why this payload cannot be processed, or None (M1).

    Every field a handler reads is checked here, so a malformed message gets a
    protocol error instead of an exception that would tear the socket down and
    fail every command in flight.
    """
    if message_type in _COMMAND_MESSAGES and not _is_uuid(payload.get("command_id")):
        return "command_id must be a UUID"

    if message_type == "agent.hello":
        if not (
            _optional(payload, "version", str)
            and _optional(payload, "operating_system", str)
        ):
            return "version and operating_system must be strings"
    elif message_type == "provider.capabilities":
        capabilities = payload.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and len(item) <= 255 for item in capabilities
        ):
            return "capabilities must be a list of strings"
    elif message_type == "command.progress":
        sequence = payload.get("sequence", 0)
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or not 0 <= sequence <= _MAX_SEQUENCE
        ):
            return "sequence must be a non-negative integer"
        event = payload.get("event", "")
        if not isinstance(event, str) or len(event) > 64:
            return "event must be a string of at most 64 characters"
        if not _optional(payload, "delta", str):
            return "delta must be a string"
    elif message_type == "session.action_required":
        if not _is_uuid(payload.get("request_id")):
            return "request_id must be a UUID"
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str) or not 0 < len(tool_name) <= 255:
            return "tool_name must be a non-empty string"
        if not _optional(payload, "input", dict):
            return "input must be an object"
        tool_use_id = payload.get("tool_use_id")
        if tool_use_id is not None and (
            not isinstance(tool_use_id, str) or len(tool_use_id) > 255
        ):
            return "tool_use_id must be a string"
        if not _optional(payload, "fingerprint", str):
            return "fingerprint must be a string"
    elif message_type == "command.completed":
        if not _optional(payload, "result", dict):
            return "result must be an object"
    elif message_type == "command.failed":
        if not _optional(payload, "error", str):
            return "error must be a string"
        if not _optional(payload, "cancelled", bool):
            return "cancelled must be a boolean"
    return None


class EventConsumer(AsyncJsonWebsocketConsumer):
    user_id: int | None = None

    async def connect(self) -> None:
        user = await sync_to_async(authenticate_events_user)(self.scope["query_string"])
        if user is None:
            await self.close(code=4401)
            return

        self.user_id = int(user.id)
        await self.channel_layer.group_add(
            f"events.user.{self.user_id}", self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code: int) -> None:
        if self.user_id is not None:
            await self.channel_layer.group_discard(
                f"events.user.{self.user_id}",
                self.channel_name,
            )

    async def fabric_event(self, event: dict[str, Any]) -> None:
        await self.send_json(event["message"])


class AgentConsumer(AsyncJsonWebsocketConsumer):
    agent: Agent | None = None
    #: Hash of the token this socket authenticated with: a rotation changes it.
    token_hash: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Fixed one-minute window of the per-socket message rate limit.
        self._window_started = time.monotonic()
        self._window_count = 0
        #: Output characters stored so far per command (progress deltas).
        self._output_used: dict[str, int] = {}

    async def connect(self) -> None:
        agent = await sync_to_async(authenticate_agent)(self.scope)
        if agent is None:
            await self.close(code=4401)
            return

        self.agent = agent
        self.token_hash = agent.development_token_hash
        # One live socket per agent: the newest one wins and the previous one
        # is closed (4409). See `_take_over` for why eviction, not refusal.
        await sync_to_async(self._take_over)()
        await self.channel_layer.group_send(
            f"agent.{agent.id}",
            {"type": "agent.disconnect", "code": 4409, "except": self.channel_name},
        )
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

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None
    ) -> None:
        """Bound every frame before it is decoded (M1).

        Size and rate are checked first, then the JSON is parsed here rather than
        by `AsyncJsonWebsocketConsumer`, whose `json.loads` would raise on a
        malformed frame and drop the socket with every command in flight.
        """
        if self.agent is None:
            await self.close(code=4401)
            return

        if not self._within_rate_limit():
            LOGGER.warning("Agent %s exceeded the message rate limit", self.agent.id)
            await self.close(code=CLOSE_RATE_LIMITED)
            return

        raw: str | bytes = text_data if text_data is not None else bytes_data or b""
        size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if size > int(settings.FABRIC_AGENT_MAX_MESSAGE_BYTES):
            LOGGER.warning(
                "Agent %s sent a %s-byte message: closing", self.agent.id, size
            )
            await self.close(code=CLOSE_TOO_BIG)
            return

        try:
            content = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            await self._protocol_error("message is not valid JSON")
            return
        if not isinstance(content, dict):
            await self._protocol_error("message must be a JSON object")
            return
        await self.receive_json(content)

    def _within_rate_limit(self) -> bool:
        now = time.monotonic()
        if now - self._window_started >= 60:
            self._window_started = now
            self._window_count = 0
        self._window_count += 1
        limit = int(settings.FABRIC_AGENT_MAX_MESSAGES_PER_MINUTE)
        return self._window_count <= limit

    async def _protocol_error(self, message: str) -> None:
        await self.send_json(
            build_message(
                message_type="error",
                payload={"code": "protocol_error", "message": message},
            )
        )

    async def receive_json(self, content: dict[str, object], **kwargs: object) -> None:
        if self.agent is None:
            await self.close(code=4401)
            return

        try:
            envelope = parse_message(content)
        except ProtocolValidationError as exc:
            await self._protocol_error(str(exc))
            return
        error = _payload_error(envelope.type, envelope.payload)
        if error is not None:
            await self._protocol_error(error)
            return

        # Authentication happened once, at the handshake: re-check on every
        # message that the token was neither revoked nor rotated and that this
        # socket was not superseded, in case the close event was lost.
        if not await sync_to_async(self._touch_agent)():
            await self.close(code=4401)
            return
        message_type = envelope.type

        if message_type == "agent.hello":
            if not await sync_to_async(update_agent_presence)(
                self.agent,
                status=AgentStatus.ONLINE,
                version=str(envelope.payload.get("version") or "")[:64],
                operating_system=str(
                    envelope.payload.get("operating_system") or ""
                )[:128],
            ):
                await self.close(code=4401)
                return
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

        if message_type == "session.action_required":
            await sync_to_async(self._register_permission_request)(envelope.payload)
            return

        if message_type == "command.completed":
            await sync_to_async(self._mark_command_completed)(envelope.payload)
            return

        if message_type == "command.failed":
            await sync_to_async(self._mark_command_failed)(envelope.payload)

    async def agent_disconnect(self, event: dict[str, object]) -> None:
        """Revocation, rotation or takeover by a newer socket: close now."""
        if event.get("except") == self.channel_name:
            return
        await self.close(code=int(str(event.get("code", 4401))))

    async def command_request(self, event: dict[str, object]) -> None:
        await self.send_json(event["message"])

    async def command_cancel(self, event: dict[str, object]) -> None:
        await self.send_json(event["message"])

    async def session_action_response(self, event: dict[str, object]) -> None:
        await self.send_json(event["message"])

    def _take_over(self) -> None:
        """Make this socket the agent's only live one.

        Eviction rather than refusal: after a network drop the server may not
        have noticed the old socket is dead yet, and refusing would lock the
        legitimate agent out until a TCP timeout. A thief racing the real agent
        gains nothing durable either way — revoking or rotating the token closes
        every socket. Commands in flight on the previous socket are orphaned:
        nothing will report on them again, so they fail now rather than after
        this socket has been handed new work.
        """
        if self.agent is None:
            return
        Agent.objects.filter(id=self.agent.id).update(active_channel=self.channel_name)
        self._fail_in_flight_commands(Agent.objects.get(id=self.agent.id))

    def _mark_offline(self) -> None:
        if self.agent is None:
            return
        # Only the socket that still holds the agent may mark it offline: a
        # socket evicted by a newer connection must leave that one alone.
        released = Agent.objects.filter(
            id=self.agent.id, active_channel=self.channel_name
        ).update(active_channel="")
        if not released:
            return
        agent = Agent.objects.get(id=self.agent.id)
        agent.mark_offline()
        self._fail_in_flight_commands(agent)
        publish_agent_updated(agent)

    @staticmethod
    def _fail_in_flight_commands(agent: Agent) -> None:
        """Nothing will ever report on these again: don't leave them running."""
        in_flight = list(
            Command.objects.filter(
                agent=agent,
                status__in=[
                    CommandStatus.DISPATCHED,
                    CommandStatus.RUNNING,
                    CommandStatus.WAITING_USER_ACTION,
                ],
            )
        )
        for command in in_flight:
            command.status = CommandStatus.FAILED
            command.error = "Agent disconnected before the command completed"
            command.finished_at = timezone.now()
            command.save(
                update_fields=["status", "error", "finished_at", "updated_at"]
            )
            sync_message_for_command_failed(command)
            publish_command_updated(command)
        if in_flight:
            notify_agent_offline(agent, len(in_flight))

    def _touch_agent(self) -> bool:
        """Refresh the agent's activity; False when this socket lost its right."""
        if self.agent is None:
            return False
        agent = Agent.objects.filter(id=self.agent.id).first()
        if (
            agent is None
            or agent.status == AgentStatus.DISABLED
            or not agent.development_token_hash
            or agent.development_token_hash != self.token_hash
            or agent.active_channel != self.channel_name
        ):
            return False
        agent.touch()
        return True

    def _set_capabilities(self, capabilities: object) -> None:
        if self.agent is None:
            return
        agent = Agent.objects.get(id=self.agent.id)
        agent.capabilities = (
            list(capabilities) if isinstance(capabilities, list) else []
        )
        agent.save(update_fields=["capabilities", "updated_at"])
        publish_agent_updated(agent)

    def _load_command(
        self,
        command_id: object,
        next_status: str | None = None,
    ) -> Command | None:
        """Load a command this agent is allowed to report on.

        Scoping on `agent` matters: without it any authenticated agent could
        write results into any other agent's commands just by guessing an id.
        `next_status` additionally enforces the documented state machine, so a
        late or replayed message cannot resurrect a finished command.
        """
        if self.agent is None:
            return None
        try:
            command = Command.objects.get(id=str(command_id), agent_id=self.agent.id)
        except (Command.DoesNotExist, ValidationError, ValueError):
            LOGGER.warning(
                "Agent %s reported on unknown command %s", self.agent.id, command_id
            )
            return None
        if next_status is not None and not command.can_transition_to(next_status):
            LOGGER.warning(
                "Ignoring %s -> %s for command %s",
                command.status,
                next_status,
                command.id,
            )
            return None
        return command

    def _mark_command_started(self, command_id: str) -> None:
        command = self._load_command(command_id, CommandStatus.RUNNING)
        if command is None:
            return
        command.status = CommandStatus.RUNNING
        command.started_at = timezone.now()
        command.save(update_fields=["status", "started_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).exclude(
            status=AgentStatus.DISABLED
        ).update(status=AgentStatus.BUSY)
        command.refresh_from_db()
        sync_message_for_command_started(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)

    def _append_command_event(self, payload: dict[str, object]) -> None:
        command = self._load_command(payload.get("command_id"))
        if command is None:
            return
        if command.status not in _ACTIVE_STATUSES:
            # A finished command takes no more output (late or replayed frame).
            LOGGER.warning(
                "Ignoring progress for %s command %s", command.status, command.id
            )
            return

        # Cap the output stored per command (I4): once the budget is spent the
        # last stored event ends with a marker and later ones are dropped.
        key = str(command.id)
        used = self._output_used.get(key, 0)
        budget = output_max_chars() - used
        if budget <= 0:
            return
        envelope_keys = {"command_id", "sequence", "event"}
        body, spent, _ = cap_strings(
            {k: v for k, v in payload.items() if k not in envelope_keys}, budget
        )
        self._output_used[key] = used + max(spent, 1)
        stored = {
            **{k: v for k, v in payload.items() if k in envelope_keys},
            **body,
        }

        try:
            with transaction.atomic():
                command_event = CommandEvent.objects.create(
                    command=command,
                    sequence=int(str(payload.get("sequence", 0))),
                    event_type=str(payload.get("event") or "message.delta"),
                    payload=stored,
                )
        except IntegrityError:
            LOGGER.warning(
                "Duplicate progress sequence %s for command %s",
                payload.get("sequence"),
                command.id,
            )
            return
        delta = stored.get("delta")
        sync_message_for_command_progress(
            command, delta if isinstance(delta, str) else ""
        )
        publish_command_event(command, command_event)

    def _register_permission_request(self, payload: dict[str, object]) -> None:
        """Record a tool call the agent is blocked on, and surface it."""
        command = self._load_command(
            payload.get("command_id"), CommandStatus.WAITING_USER_ACTION
        )
        if command is None:
            return

        request, created = PermissionRequest.objects.get_or_create(
            request_id=str(payload.get("request_id", "")),
            defaults={
                "command": command,
                "tool_name": str(payload.get("tool_name", "")),
                "tool_input": cap_strings(
                    payload.get("input") or {}, TOOL_INPUT_MAX_CHARS
                )[0],
                "tool_use_id": str(payload.get("tool_use_id") or ""),
                "fingerprint": _fingerprint(payload.get("fingerprint")),
            },
        )
        if not created:
            return

        command.status = CommandStatus.WAITING_USER_ACTION
        command.save(update_fields=["status", "updated_at"])
        publish_permission_request(command, request)
        publish_command_updated(command)
        # The operator may not be looking at the browser: the whole point of the
        # approval bridge is being reachable.
        notify_permission_request(command, request)

    def _mark_command_completed(self, payload: dict[str, object]) -> None:
        command = self._load_command(
            payload.get("command_id"), CommandStatus.SUCCEEDED
        )
        if command is None:
            return
        command.status = CommandStatus.SUCCEEDED
        command.result, _, _ = cap_strings(
            payload.get("result") or {}, output_max_chars()
        )
        self._output_used.pop(str(command.id), None)
        command.finished_at = timezone.now()
        command.save(update_fields=["status", "result", "finished_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).exclude(
            status=AgentStatus.DISABLED
        ).update(status=AgentStatus.ONLINE)
        command.refresh_from_db()
        sync_message_for_command_completed(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)
        notify_command_finished(command)

    def _mark_command_failed(self, payload: dict[str, object]) -> None:
        is_cancelled = payload.get("cancelled") is True
        next_status = (
            CommandStatus.CANCELLED if is_cancelled else CommandStatus.FAILED
        )
        command = self._load_command(payload.get("command_id"), next_status)
        if command is None:
            return
        command.status = next_status
        command.error = cap_text(
            str(payload.get("error") or "Unknown error"), ERROR_MAX_CHARS
        )
        self._output_used.pop(str(command.id), None)
        command.finished_at = timezone.now()
        command.save(update_fields=["status", "error", "finished_at", "updated_at"])
        Agent.objects.filter(id=command.agent_id).exclude(
            status=AgentStatus.DISABLED
        ).update(status=AgentStatus.ONLINE if is_cancelled else AgentStatus.ERROR)
        command.refresh_from_db()
        sync_message_for_command_failed(command)
        agent = Agent.objects.get(id=command.agent_id)
        publish_command_updated(command)
        publish_agent_updated(agent)
        notify_command_finished(command)
