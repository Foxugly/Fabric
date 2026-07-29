from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.db import models


class MessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"
    TOOL = "tool", "Tool"


class MessageStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    provider = models.CharField(max_length=64)
    external_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255, blank=True, default="New conversation")
    url = models.URLField(blank=True)
    agent = models.ForeignKey(
        "agents.Agent",
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_accessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.provider}:{self.title} ({self.id})"


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    command = models.OneToOneField(
        "commands.Command",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_message",
    )
    role = models.CharField(max_length=16, choices=MessageRole.choices)
    content = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=MessageStatus.choices,
        default=MessageStatus.SUCCEEDED,
    )
    external_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "updated_at"]

    def __str__(self) -> str:
        return f"{self.role}:{self.id}"
