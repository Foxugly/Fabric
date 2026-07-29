from __future__ import annotations

from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from shared.protocol import all_qualified_actions


class CommandStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DISPATCHED = "dispatched", "Dispatched"
    RUNNING = "running", "Running"
    WAITING_USER_ACTION = "waiting_user_action", "Waiting user action"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    TIMED_OUT = "timed_out", "Timed out"


#: Derived from `shared/protocol/actions.py` so the API and the agent can never
#: disagree on what a valid action is.
ALLOWED_ACTIONS = all_qualified_actions()

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    str(CommandStatus.PENDING): {
        str(CommandStatus.DISPATCHED),
        str(CommandStatus.CANCELLED),
    },
    str(CommandStatus.DISPATCHED): {
        str(CommandStatus.RUNNING),
        str(CommandStatus.WAITING_USER_ACTION),
        str(CommandStatus.FAILED),
        str(CommandStatus.CANCELLED),
    },
    str(CommandStatus.RUNNING): {
        str(CommandStatus.SUCCEEDED),
        str(CommandStatus.WAITING_USER_ACTION),
        str(CommandStatus.FAILED),
        str(CommandStatus.TIMED_OUT),
        str(CommandStatus.CANCELLED),
    },
    str(CommandStatus.WAITING_USER_ACTION): {
        str(CommandStatus.RUNNING),
        str(CommandStatus.CANCELLED),
        str(CommandStatus.FAILED),
    },
    str(CommandStatus.SUCCEEDED): set(),
    str(CommandStatus.FAILED): set(),
    str(CommandStatus.CANCELLED): set(),
    str(CommandStatus.TIMED_OUT): set(),
}


class Command(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="commands",
        null=True,
        blank=True,
    )
    agent = models.ForeignKey(
        "agents.Agent", on_delete=models.CASCADE, related_name="commands"
    )
    conversation = models.ForeignKey(
        "conversations.Conversation",
        on_delete=models.CASCADE,
        related_name="commands",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=64)
    action = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=32, choices=CommandStatus.choices, default=CommandStatus.PENDING
    )
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    timeout_seconds = models.PositiveIntegerField(default=120)
    correlation_id = models.UUIDField(default=uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.action} ({self.id})"

    def clean(self) -> None:
        if self.action not in ALLOWED_ACTIONS:
            raise ValidationError({"action": "Unsupported action"})

    def can_transition_to(self, next_status: str) -> bool:
        return next_status in ALLOWED_TRANSITIONS[self.status]


class PermissionDecision(models.TextChoices):
    PENDING = "pending", "Pending"
    ALLOWED = "allowed", "Allowed"
    DENIED = "denied", "Denied"
    EXPIRED = "expired", "Expired"


class PermissionRequest(models.Model):
    """A tool call Claude Code cannot run until a human says so.

    In `--print` mode the CLI has no dialog to draw, so it delegates to Fabric
    through an MCP prompt tool. Each row is one pending question; the turn is
    blocked on the machine until it is answered.
    """

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    command = models.ForeignKey(
        Command,
        on_delete=models.CASCADE,
        related_name="permission_requests",
    )
    #: Correlates with the request the agent is blocked on.
    request_id = models.UUIDField(unique=True)
    tool_name = models.CharField(max_length=255)
    tool_input = models.JSONField(default=dict, blank=True)
    tool_use_id = models.CharField(max_length=255, blank=True)
    decision = models.CharField(
        max_length=16,
        choices=PermissionDecision.choices,
        default=PermissionDecision.PENDING,
    )
    decision_message = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="permission_decisions",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.tool_name} ({self.decision})"

    @property
    def is_pending(self) -> bool:
        return self.decision == PermissionDecision.PENDING


class CommandEvent(models.Model):
    command = models.ForeignKey(
        Command, on_delete=models.CASCADE, related_name="events"
    )
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["command", "sequence"], name="unique_command_sequence"
            )
        ]

    def __str__(self) -> str:
        return f"{self.command_id}#{self.sequence}"
