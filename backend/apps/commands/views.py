from __future__ import annotations

from typing import Any

from rest_framework import response, serializers, status, viewsets
from rest_framework.decorators import action

from apps.agents.events import publish_command_updated
from apps.agents.services import dispatch_command, dispatch_command_cancel
from apps.commands.models import Command, CommandStatus
from apps.commands.serializers import CommandCreateSerializer, CommandSerializer
from apps.commands.services import reap_stale_commands


class CommandViewSet(viewsets.GenericViewSet[Command]):
    queryset = Command.objects.select_related("agent", "conversation").prefetch_related(
        "events"
    )

    def get_queryset(self) -> Any:
        # Opportunistic reaping: Fabric has no scheduler, and a stuck command
        # must not survive a page refresh.
        reap_stale_commands()
        return (
            super()
            .get_queryset()
            .filter(requested_by_id=self.request.user.id)
            .order_by("-created_at")
        )

    def get_serializer_class(self) -> type[serializers.BaseSerializer[Any]]:
        if self.action == "create":
            return CommandCreateSerializer
        return CommandSerializer

    def list(self, request: Any) -> response.Response:
        serializer = CommandSerializer(
            self.get_queryset(),
            many=True,
        )
        return response.Response(serializer.data)

    def retrieve(self, request: Any, pk: str | None = None) -> response.Response:
        serializer = CommandSerializer(self.get_object())
        return response.Response(serializer.data)

    def create(self, request: Any) -> response.Response:
        serializer = CommandCreateSerializer(
            data=request.data,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        command = Command.objects.create(
            requested_by=request.user,
            agent=serializer.validated_data["agent"],
            conversation=serializer.validated_data.get("conversation"),
            provider=serializer.validated_data["provider"],
            action=serializer.validated_data["action"],
            payload=serializer.validated_data["payload"],
            timeout_seconds=serializer.validated_data["timeout_seconds"],
        )
        dispatch_command(command)
        output = CommandSerializer(command)
        return response.Response(output.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request: Any, pk: str | None = None) -> response.Response:
        command = self.get_object()

        if command.status == CommandStatus.PENDING:
            command.status = CommandStatus.CANCELLED
            command.error = "Command cancelled"
            command.save(update_fields=["status", "error", "updated_at"])
            publish_command_updated(command)
            return response.Response(CommandSerializer(command).data)

        if command.status in {
            CommandStatus.DISPATCHED,
            CommandStatus.RUNNING,
            CommandStatus.WAITING_USER_ACTION,
        }:
            dispatch_command_cancel(command)
            return response.Response(
                {"id": str(command.id), "status": command.status},
                status=status.HTTP_202_ACCEPTED,
            )

        return response.Response(
            {"detail": f"Command cannot be cancelled from status {command.status}."},
            status=status.HTTP_400_BAD_REQUEST,
        )
