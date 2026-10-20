from __future__ import annotations

import hashlib
import secrets
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


class AgentStatus(models.TextChoices):
    OFFLINE = "offline", "Offline"
    CONNECTING = "connecting", "Connecting"
    ONLINE = "online", "Online"
    BUSY = "busy", "Busy"
    ERROR = "error", "Error"
    DISABLED = "disabled", "Disabled"


class Agent(models.Model):
    issued_token: str | None = None

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    #: An agent grants remote code execution on the machine it runs on. Only its
    #: owner (and staff) may see it, drive it or mint tokens for it. Legacy rows
    #: may be ownerless: those are staff-only.
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agents",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=32, choices=AgentStatus.choices, default=AgentStatus.OFFLINE
    )
    version = models.CharField(max_length=64, blank=True)
    operating_system = models.CharField(max_length=128, blank=True)
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    development_token_hash = models.CharField(max_length=64, blank=True)
    #: Channel name of the one live agent socket. A new connection takes it
    #: over (and closes the previous socket); a socket that no longer holds it
    #: is closed on its next message and must not mark the agent offline.
    active_channel = models.CharField(max_length=255, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"

    def issue_development_token(self) -> str:
        """Mint a new token; the previous one stops working at once.

        Issuing a token is the explicit action that brings a revoked agent
        back: a DISABLED agent returns to OFFLINE and may connect again with
        the new token only. Nothing else ever leaves DISABLED.
        """
        raw_token = secrets.token_urlsafe(32)
        self.development_token_hash = self.hash_token(raw_token)
        self.issued_token = raw_token
        update_fields = ["development_token_hash", "updated_at"]
        if self.status == AgentStatus.DISABLED:
            self.status = AgentStatus.OFFLINE
            update_fields.insert(0, "status")
        self.save(update_fields=update_fields)
        return raw_token

    def check_development_token(self, raw_token: str) -> bool:
        if not self.development_token_hash:
            return False
        return secrets.compare_digest(
            self.development_token_hash, self.hash_token(raw_token)
        )

    def mark_online(self, *, version: str = "", operating_system: str = "") -> None:
        now = timezone.now()
        self.status = AgentStatus.ONLINE
        self.version = version or self.version
        self.operating_system = operating_system or self.operating_system
        self.last_connected_at = now
        self.last_activity_at = now
        self.save(
            update_fields=[
                "status",
                "version",
                "operating_system",
                "last_connected_at",
                "last_activity_at",
                "updated_at",
            ]
        )

    def mark_offline(self) -> None:
        # A socket closing must never undo a revocation.
        if self.status == AgentStatus.DISABLED:
            return
        self.status = AgentStatus.OFFLINE
        self.save(update_fields=["status", "updated_at"])

    def touch(self) -> None:
        update_fields = ["last_activity_at", "updated_at"]
        if self.status in {
            AgentStatus.OFFLINE,
            AgentStatus.CONNECTING,
            AgentStatus.ERROR,
        }:
            self.status = AgentStatus.ONLINE
            update_fields.insert(0, "status")
        self.last_activity_at = timezone.now()
        self.save(update_fields=update_fields)

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
