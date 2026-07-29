from __future__ import annotations

from typing import Any
from uuid import UUID

from rest_framework import response, serializers, status, viewsets

from apps.agents.services import dispatch_command
from apps.commands.models import Command
from apps.conversations.models import Conversation, Message
from apps.conversations.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from apps.conversations.services import create_message_pair_for_command

PROVIDER_ACTIONS: dict[str, str] = {
    "echo": "echo.message.send",
    "claude_code_local": "claude_code_local.message.send",
}


class ConversationViewSet(viewsets.GenericViewSet[Conversation]):
    serializer_class = ConversationSerializer

    def get_queryset(self) -> Any:
        return (
            Conversation.objects.filter(owner_id=self.request.user.id)
            .select_related("agent")
        )

    def get_serializer_class(self) -> type[serializers.BaseSerializer[Any]]:
        if self.action == "create":
            return ConversationCreateSerializer
        return ConversationSerializer

    def list(self, request: Any) -> response.Response:
        serializer = ConversationSerializer(self.get_queryset(), many=True)
        return response.Response(serializer.data)

    def retrieve(self, request: Any, pk: str | None = None) -> response.Response:
        serializer = ConversationSerializer(self.get_object())
        return response.Response(serializer.data)

    def create(self, request: Any) -> response.Response:
        serializer = ConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.validated_data["agent_id"]
        conversation = Conversation.objects.create(
            owner=request.user,
            agent=agent,
            provider=serializer.validated_data["provider"],
            title=serializer.validated_data.get("title") or "New conversation",
        )
        output = ConversationSerializer(conversation)
        return response.Response(output.data, status=status.HTTP_201_CREATED)


class MessageViewSet(viewsets.GenericViewSet[Message]):
    serializer_class = MessageSerializer

    def get_queryset(self) -> Any:
        conversation_id = self.kwargs["conversation_pk"]
        return Message.objects.filter(
            conversation__owner_id=self.request.user.id,
            conversation_id=conversation_id,
        ).select_related("conversation", "command")

    def list(
        self, request: Any, conversation_pk: UUID | None = None
    ) -> response.Response:
        serializer = MessageSerializer(self.get_queryset(), many=True)
        return response.Response(serializer.data)

    def create(
        self, request: Any, conversation_pk: UUID | None = None
    ) -> response.Response:
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if conversation_pk is None:
            raise serializers.ValidationError(
                {"conversation": "Conversation is required"}
            )
        conversation = Conversation.objects.get(
            id=conversation_pk,
            owner_id=request.user.id,
        )
        action = PROVIDER_ACTIONS.get(conversation.provider)
        if action is None:
            raise serializers.ValidationError(
                {"provider": f"Unsupported provider {conversation.provider}"}
            )

        command = Command.objects.create(
            requested_by=request.user,
            agent=conversation.agent,
            conversation=conversation,
            provider=conversation.provider,
            action=action,
            payload={"text": serializer.validated_data["content"]},
        )
        create_message_pair_for_command(
            conversation_id=str(conversation.id),
            user_content=serializer.validated_data["content"],
            command=command,
        )
        dispatch_command(command)
        output = MessageSerializer(command.assistant_message)
        return response.Response(
            {
                "message": output.data,
                "command_id": str(command.id),
                "status": command.status,
            },
            status=status.HTTP_201_CREATED,
        )
