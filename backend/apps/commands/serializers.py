from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers
from shared.protocol import qualified_actions_by_provider

from apps.agents.models import Agent
from apps.agents.views import visible_agents_for
from apps.commands.models import (
    ALLOWED_ACTIONS,
    Command,
    CommandEvent,
    PermissionRequest,
)

ALLOWED_PROVIDER_ACTIONS: dict[str, set[str]] = qualified_actions_by_provider()


def resolve_owned_agent(context: Mapping[str, Any], agent_id: Any) -> Agent:
    """Resolve an agent the caller is actually allowed to drive.

    Unknown and not-yours are deliberately indistinguishable: an agent id is a
    capability, and confirming its existence to a stranger is already a leak.
    """
    request = context.get("request")
    user = getattr(request, "user", None)
    try:
        return visible_agents_for(user).get(id=agent_id)
    except Agent.DoesNotExist as exc:
        raise serializers.ValidationError({"agent_id": "Unknown agent"}) from exc


class CommandEventSerializer(serializers.ModelSerializer[CommandEvent]):
    class Meta:
        model = CommandEvent
        fields = ["sequence", "event_type", "payload", "created_at"]


class PermissionRequestSerializer(serializers.ModelSerializer[PermissionRequest]):
    id = serializers.UUIDField(read_only=True)
    command = serializers.UUIDField(source="command_id", read_only=True)

    class Meta:
        model = PermissionRequest
        fields = [
            "id",
            "command",
            "request_id",
            "tool_name",
            "tool_input",
            "tool_use_id",
            "decision",
            "decision_message",
            "created_at",
            "decided_at",
        ]
        read_only_fields = fields


class PermissionDecisionSerializer(serializers.Serializer[dict[str, Any]]):
    behavior = serializers.ChoiceField(choices=["allow", "deny"])
    message = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=2000,
    )


class CommandSerializer(serializers.ModelSerializer[Command]):
    id = serializers.UUIDField(read_only=True)
    conversation = serializers.UUIDField(source="conversation_id", read_only=True)
    agent = serializers.UUIDField(source="agent_id", read_only=True)
    correlation_id = serializers.UUIDField(read_only=True)
    events = CommandEventSerializer(many=True, read_only=True)
    permission_requests = PermissionRequestSerializer(many=True, read_only=True)

    class Meta:
        model = Command
        fields = [
            "id",
            "conversation",
            "agent",
            "provider",
            "action",
            "payload",
            "status",
            "result",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "timeout_seconds",
            "correlation_id",
            "events",
            "permission_requests",
        ]
        read_only_fields = [
            "status",
            "result",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "correlation_id",
            "events",
            "permission_requests",
        ]


class CommandCreateSerializer(serializers.Serializer[dict[str, Any]]):
    agent_id = serializers.UUIDField()
    provider = serializers.CharField(max_length=64)
    action = serializers.ChoiceField(choices=sorted(ALLOWED_ACTIONS))
    payload = serializers.JSONField()
    timeout_seconds = serializers.IntegerField(min_value=1, max_value=3600, default=120)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs["agent"] = resolve_owned_agent(self.context, attrs["agent_id"])
        provider = attrs["provider"]
        action = attrs["action"]
        if provider not in ALLOWED_PROVIDER_ACTIONS:
            raise serializers.ValidationError({"provider": "Unsupported provider"})
        if action not in ALLOWED_PROVIDER_ACTIONS[provider]:
            raise serializers.ValidationError(
                {"action": f"Unsupported action for provider {provider}"}
            )
        return attrs
