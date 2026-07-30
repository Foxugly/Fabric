"""Per-user notification targets.

Mirrors `accounts.PushItTarget` in FoxRunner_server deliberately: same field
names, same "one default per owner" rule, same principle that a notification
follows *whoever owns the thing* rather than a single shared site token.

Why per-user rather than SSM: Fabric has owners now (`Agent.owner`), so "who to
notify" is user data, not site configuration. A token in SSM would send every
operator's approvals to the same phone, and changing your own preference would
need AWS access plus a service restart.

The site-level `PUSHIT_*` settings remain as a fallback for a user who has no
target of their own.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models


class PushItTarget(models.Model):
    """A PushIT application this user wants Fabric to notify.

    Each row belongs to one user and is never shared: every query is scoped to
    `request.user`. `app_token` is the PushIT app token (header `X-App-Token`);
    it is a secret, but it is returned to its own owner so the profile editor
    can display and change it. It is send-only — it cannot read notifications.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pushit_targets",
    )
    name = models.CharField(max_length=64)
    app_token = models.CharField(max_length=255)
    base_url = models.CharField(
        max_length=255,
        default="https://pushit-api.foxugly.com",
    )
    title = models.CharField(max_length=128, default="Fabric")
    #: Muting without losing the token: keep the row, stop the notifications.
    enabled = models.BooleanField(default=True)
    #: Which events to notify. Empty means "the site defaults"
    #: (`settings.PUSHIT_DEFAULT_EVENTS`), so a fresh target behaves sensibly.
    events = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pushit_targets"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="uq_pushit_target_owner_name"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.owner_id})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        # A user with a single target should not have to tick "default", and two
        # defaults would make the choice arbitrary.
        siblings = type(self).objects.filter(owner_id=self.owner_id).exclude(pk=self.pk)
        if not siblings.exists():
            self.is_default = True
        super().save(*args, **kwargs)
        if self.is_default:
            type(self).objects.filter(
                owner_id=self.owner_id, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)

    def event_policy(self) -> dict[str, bool]:
        """This target's policy, filled in from the site defaults."""
        policy = dict(getattr(settings, "PUSHIT_DEFAULT_EVENTS", {}))
        configured = self.events if isinstance(self.events, dict) else {}
        for name in policy:
            if name in configured:
                policy[name] = bool(configured[name])
        return policy

    def wants(self, event: str) -> bool:
        return self.enabled and self.event_policy().get(event, False)
