from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.agents.models import Agent
from apps.conversations.models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer[Conversation]):
    id = serializers.UUIDField(read_only=True)
    agent = serializers.UUIDField(source="agent_id", read_only=True)

    class Meta:
        model = Conversation
        fields = [
            "id",
            "provider",
            "external_id",
            "title",
            "url",
            "agent",
            "created_at",
            "updated_at",
            "last_accessed_at",
        ]
        read_only_fields = [
            "id",
            "external_id",
            "url",
            "created_at",
            "updated_at",
            "last_accessed_at",
        ]


class ConversationCreateSerializer(serializers.Serializer[dict[str, Any]]):
    agent_id = serializers.UUIDField()
    provider = serializers.CharField(max_length=64, default="claude_code_local")
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate_agent_id(self, value: str) -> Agent:
        try:
            return Agent.objects.get(id=value)
        except Agent.DoesNotExist as exc:
            raise serializers.ValidationError("Unknown agent") from exc


class MessageSerializer(serializers.ModelSerializer[Message]):
    id = serializers.UUIDField(read_only=True)
    conversation = serializers.UUIDField(source="conversation_id", read_only=True)
    command_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Message
        fields = [
            "id",
            "conversation",
            "command_id",
            "role",
            "content",
            "status",
            "external_id",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "conversation",
            "command_id",
            "role",
            "status",
            "external_id",
            "metadata",
            "created_at",
            "updated_at",
        ]


class MessageCreateSerializer(serializers.Serializer[dict[str, Any]]):
    content = serializers.CharField(allow_blank=False)
