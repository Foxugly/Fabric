from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.agents.models import Agent
from apps.commands.models import ALLOWED_ACTIONS, Command, CommandEvent

ALLOWED_PROVIDER_ACTIONS: dict[str, set[str]] = {
    "echo": {"echo.message.send"},
    "claude_code_local": {
        "claude_code_local.session.status",
        "claude_code_local.message.send",
    },
    "windows_powershell": {
        "windows_powershell.system.info",
        "windows_powershell.process.list",
        "windows_powershell.claude.version",
        "windows_powershell.session.create",
        "windows_powershell.session.status",
        "windows_powershell.session.close",
        "windows_powershell.command.run",
        "windows_powershell.network.recover",
    },
}


class CommandEventSerializer(serializers.ModelSerializer[CommandEvent]):
    class Meta:
        model = CommandEvent
        fields = ["sequence", "event_type", "payload", "created_at"]


class CommandSerializer(serializers.ModelSerializer[Command]):
    id = serializers.UUIDField(read_only=True)
    conversation = serializers.UUIDField(source="conversation_id", read_only=True)
    agent = serializers.UUIDField(source="agent_id", read_only=True)
    correlation_id = serializers.UUIDField(read_only=True)
    events = CommandEventSerializer(many=True, read_only=True)

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
        ]


class CommandCreateSerializer(serializers.Serializer[dict[str, Any]]):
    agent_id = serializers.UUIDField()
    provider = serializers.CharField(max_length=64)
    action = serializers.ChoiceField(choices=sorted(ALLOWED_ACTIONS))
    payload = serializers.JSONField()
    timeout_seconds = serializers.IntegerField(min_value=1, max_value=3600, default=120)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        agent_id = attrs["agent_id"]
        try:
            attrs["agent"] = Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist as exc:
            raise serializers.ValidationError({"agent_id": "Unknown agent"}) from exc
        provider = attrs["provider"]
        action = attrs["action"]
        if provider not in ALLOWED_PROVIDER_ACTIONS:
            raise serializers.ValidationError({"provider": "Unsupported provider"})
        if action not in ALLOWED_PROVIDER_ACTIONS[provider]:
            raise serializers.ValidationError(
                {"action": f"Unsupported action for provider {provider}"}
            )
        return attrs
