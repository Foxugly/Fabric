from __future__ import annotations

from django.utils import timezone

from apps.commands.models import Command
from apps.conversations.models import Message, MessageRole, MessageStatus


def sync_message_for_command_started(command: Command) -> None:
    if not hasattr(command, "assistant_message"):
        return
    message = command.assistant_message
    message.status = MessageStatus.RUNNING
    message.save(update_fields=["status", "updated_at"])


def sync_message_for_command_progress(command: Command, delta: str) -> None:
    if not hasattr(command, "assistant_message"):
        return
    message = command.assistant_message
    message.status = MessageStatus.RUNNING
    message.content = f"{message.content}{delta}"
    message.save(update_fields=["status", "content", "updated_at"])
    _touch_conversation(message)


def sync_message_for_command_completed(command: Command) -> None:
    if not hasattr(command, "assistant_message"):
        return
    message = command.assistant_message
    result_text = _extract_result_text(command)
    if result_text:
        message.content = result_text
    message.status = MessageStatus.SUCCEEDED
    message.save(update_fields=["status", "content", "updated_at"])
    _touch_conversation(message)


def sync_message_for_command_failed(command: Command) -> None:
    if not hasattr(command, "assistant_message"):
        return
    message = command.assistant_message
    message.status = MessageStatus.FAILED
    message.metadata = {**message.metadata, "error": command.error}
    message.save(update_fields=["status", "metadata", "updated_at"])
    _touch_conversation(message)


def create_message_pair_for_command(
    *,
    conversation_id: str,
    user_content: str,
    command: Command,
) -> None:
    Message.objects.create(
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=user_content,
        status=MessageStatus.SUCCEEDED,
    )
    Message.objects.create(
        conversation_id=conversation_id,
        command=command,
        role=MessageRole.ASSISTANT,
        content="",
        status=MessageStatus.PENDING,
    )


def _extract_result_text(command: Command) -> str:
    if "text" in command.result and isinstance(command.result["text"], str):
        return command.result["text"]
    if "response_text" in command.result and isinstance(
        command.result["response_text"], str
    ):
        return command.result["response_text"]
    if hasattr(command, "assistant_message"):
        return command.assistant_message.content
    return ""


def _touch_conversation(message: Message) -> None:
    message.conversation.last_accessed_at = timezone.now()
    message.conversation.save(update_fields=["last_accessed_at", "updated_at"])
