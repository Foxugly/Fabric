from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.agents.models import Agent


class AgentSerializer(serializers.ModelSerializer[Agent]):
    class Meta:
        model = Agent
        fields = [
            "id",
            "owner",
            "name",
            "description",
            "status",
            "version",
            "operating_system",
            "last_connected_at",
            "last_activity_at",
            "capabilities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "owner",
            "status",
            "version",
            "operating_system",
            "last_connected_at",
            "last_activity_at",
            "capabilities",
            "created_at",
            "updated_at",
        ]


class AgentCreateSerializer(serializers.ModelSerializer[Agent]):
    development_token = serializers.CharField(read_only=True)

    class Meta:
        model = Agent
        fields = ["id", "name", "description", "development_token", "status"]
        read_only_fields = ["id", "development_token", "status"]

    def create(self, validated_data: dict[str, Any]) -> Agent:
        agent = Agent.objects.create(**validated_data)
        agent.issue_development_token()
        return agent

    def to_representation(self, instance: Agent) -> dict[str, Any]:
        data = dict(super().to_representation(instance))
        data["development_token"] = instance.issued_token
        return data
