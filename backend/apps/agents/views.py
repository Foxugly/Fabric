from __future__ import annotations

from typing import Any

from rest_framework import decorators, response, status, viewsets

from apps.agents.models import Agent, AgentStatus
from apps.agents.serializers import AgentCreateSerializer, AgentSerializer


class AgentViewSet(viewsets.ModelViewSet[Agent]):
    queryset = Agent.objects.order_by("name")

    def get_queryset(self) -> Any:
        return visible_agents_for(self.request.user)

    def get_serializer_class(self) -> type[AgentSerializer | AgentCreateSerializer]:
        if self.action == "create":
            return AgentCreateSerializer
        return AgentSerializer

    def perform_create(self, serializer: Any) -> None:
        serializer.save(owner=self.request.user)

    @decorators.action(detail=True, methods=["post"], url_path="development-token")
    def development_token(
        self,
        request: Any,
        pk: str | None = None,
    ) -> response.Response:
        agent = self.get_object()
        token = agent.issue_development_token()
        # Rotation: a socket opened with the previous token must not survive it.
        _disconnect_agent_sockets(agent.id)
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
        _disconnect_agent_sockets(agent.id)
        return response.Response({"status": agent.status}, status=status.HTTP_200_OK)


def visible_agents_for(user: Any) -> Any:
    """Agents a user may see and drive.

    Driving an agent means running code on someone's machine, so this is the
    authorisation boundary for the whole product. Everyone — staff and
    superusers included — sees only their own agents: an admin flag must not
    double as root on every registered PC. Ownerless legacy agents are visible
    to nobody until an owner is assigned (Django admin or shell).
    """
    return Agent.objects.order_by("name").filter(owner_id=getattr(user, "id", None))


def _disconnect_agent_sockets(agent_id: object) -> None:
    # Imported late: services -> events -> commands.serializers imports this
    # module for `visible_agents_for`.
    from apps.agents.services import disconnect_agent_sockets

    disconnect_agent_sockets(agent_id)
