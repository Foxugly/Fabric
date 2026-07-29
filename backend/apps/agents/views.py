from __future__ import annotations

from typing import Any

from rest_framework import decorators, response, status, viewsets

from apps.agents.models import Agent, AgentStatus
from apps.agents.serializers import AgentCreateSerializer, AgentSerializer


class AgentViewSet(viewsets.ModelViewSet[Agent]):
    queryset = Agent.objects.order_by("name")

    def get_serializer_class(self) -> type[AgentSerializer | AgentCreateSerializer]:
        if self.action == "create":
            return AgentCreateSerializer
        return AgentSerializer

    @decorators.action(detail=True, methods=["post"], url_path="development-token")
    def development_token(
        self,
        request: Any,
        pk: str | None = None,
    ) -> response.Response:
        agent = self.get_object()
        token = agent.issue_development_token()
        return response.Response(
            {"agent_id": str(agent.id), "development_token": token},
            status=status.HTTP_200_OK,
        )

    @decorators.action(detail=True, methods=["post"], url_path="revoke")
    def revoke(self, request: Any, pk: str | None = None) -> response.Response:
        agent = self.get_object()
        agent.status = AgentStatus.DISABLED
        agent.development_token_hash = ""
        agent.save(update_fields=["status", "development_token_hash", "updated_at"])
        return response.Response({"status": agent.status}, status=status.HTTP_200_OK)
